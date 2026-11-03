import requests
import json
import base64
import math
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile

import geopandas as gpd
import streamlit as st
import pydeck as pdk
import folium
from folium.plugins import Draw
from pyproj import Geod
from shapely.geometry import Point, box, mapping, shape
import pandas as pd
from streamlit_folium import st_folium
from urllib.parse import quote, urlencode


GEOD_WGS84 = Geod(ellps="WGS84")


@st.cache_data(ttl=3600, max_entries=256, show_spinner=False)
def recuperer_contour_commune(code_insee: str):
    """Récupère le contour GeoJSON d'une commune."""
    response = requests.get(
        f"https://geo.api.gouv.fr/communes/{code_insee}",
        params={"fields": "nom,contour", "format": "geojson", "geometry": "contour"},
        timeout=20,
    )
    response.raise_for_status()
    feature = response.json()
    if not feature.get("geometry"):
        raise ValueError(f"Le contour de la commune {code_insee} est indisponible.")
    return feature


@st.cache_data(ttl=3600, max_entries=32, show_spinner=False)
def recuperer_image_wms_emprise(
    service_url: str,
    couche: str,
    emprise: dict,
    taille_max: int = 2200,
):
    """Récupère une couche cartographique WMS dans une emprise donnée."""
    geometrie = shape(emprise["geometry"])
    min_lon, min_lat, max_lon, max_lat = geometrie.bounds
    ratio = (max_lon - min_lon) / max(max_lat - min_lat, 1e-9)
    if ratio >= 1:
        largeur = taille_max
        hauteur = max(256, round(largeur / ratio))
    else:
        hauteur = taille_max
        largeur = max(256, round(hauteur * ratio))

    response = requests.get(
        service_url,
        params={
            "SERVICE": "WMS",
            "VERSION": "1.3.0",
            "REQUEST": "GetMap",
            "LAYERS": couche,
            "STYLES": "",
            "CRS": "CRS:84",
            "BBOX": f"{min_lon},{min_lat},{max_lon},{max_lat}",
            "WIDTH": largeur,
            "HEIGHT": hauteur,
            "FORMAT": "image/png",
            "TRANSPARENT": "TRUE",
        },
        timeout=45,
    )
    response.raise_for_status()
    if not response.headers.get("Content-Type", "").startswith("image/"):
        raise ValueError(f"Le service cartographique n'a pas renvoyé {couche}.")
    image = base64.b64encode(response.content).decode("ascii")
    return image, [min_lon, min_lat, max_lon, max_lat]


@st.cache_data(ttl=86400, max_entries=12, show_spinner=False)
def recuperer_legende_wms(service_url: str, couche: str, style: str):
    """Récupère la légende officielle d'une couche WMS."""
    response = requests.get(
        service_url,
        params={
            "SERVICE": "WMS",
            "VERSION": "1.1.1",
            "REQUEST": "GetLegendGraphic",
            "LAYER": couche,
            "FORMAT": "image/png",
            "STYLE": style,
        },
        timeout=30,
    )
    response.raise_for_status()
    if not response.headers.get("Content-Type", "").startswith("image/"):
        raise ValueError(f"Le service cartographique n'a pas fourni la légende de {couche}.")
    return response.content


@st.cache_data(ttl=86400, max_entries=2, show_spinner=False)
def recuperer_archive_geologie(departement: str):
    """Télécharge l'archive départementale de la BD CHARM 50 du BRGM."""
    url = (
        "https://data.cquest.org/brgm/bd_charm_50/2019/"
        f"GEO050K_HARM_{departement}.zip"
    )
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    return response.content


def creer_emprise_carree_1km(longitude: float, latitude: float) -> dict:
    """Crée un carré de 1 km² centré sur l'adresse en Lambert-93."""
    point = gpd.GeoSeries(
        [Point(longitude, latitude)],
        crs="EPSG:4326",
    ).to_crs("EPSG:2154").iloc[0]
    demi_cote = 500
    carre = box(
        point.x - demi_cote,
        point.y - demi_cote,
        point.x + demi_cote,
        point.y + demi_cote,
    )
    geographie = gpd.GeoSeries([carre], crs="EPSG:2154").to_crs("EPSG:4326").iloc[0]
    return {
        "type": "Feature",
        "properties": {},
        "geometry": mapping(geographie),
    }


@st.cache_data(ttl=86400, max_entries=8, show_spinner=False)
def recuperer_geologie_vectorielle_zone(code_insee: str, emprise: dict):
    """Charge les unités géologiques BD CHARM 50 dans une emprise donnée."""
    departement = (
        code_insee[:3]
        if code_insee.startswith(("97", "98"))
        else code_insee[:2]
    )
    departement_archive = (
        f"0{departement}" if len(departement) == 2 else departement
    )
    archive_data = recuperer_archive_geologie(departement_archive)
    suffixe_couche = f"_{departement_archive}_S_FGEOL_2154.shp"

    try:
        with ZipFile(BytesIO(archive_data)) as archive:
            membres = archive.namelist()
            shapefile = next(
                (
                    membre
                    for membre in membres
                    if Path(membre).name.endswith(suffixe_couche)
                ),
                None,
            )
            if shapefile is None:
                raise ValueError(
                    f"L'archive BRGM du département {departement} "
                    "ne contient pas la couche des formations géologiques."
                )

            nom_couche = Path(shapefile).name[:-4]
            with TemporaryDirectory(prefix="bd_charm_50_") as dossier_temporaire:
                for extension in (".shp", ".shx", ".dbf", ".prj"):
                    nom_membre = f"{nom_couche}{extension}"
                    membre = next(
                        (
                            item
                            for item in membres
                            if Path(item).name == nom_membre
                        ),
                        None,
                    )
                    if membre is None:
                        raise ValueError(
                            f"Le fichier {nom_membre} manque dans "
                            f"l'archive BRGM du département {departement}."
                        )
                    (Path(dossier_temporaire) / nom_membre).write_bytes(
                        archive.read(membre)
                    )

                geologie = gpd.read_file(
                    Path(dossier_temporaire) / f"{nom_couche}.shp"
                )
    except BadZipFile as exc:
        raise ValueError("L'archive géologique BRGM est invalide.") from exc

    if geologie.crs is None:
        raise ValueError("Le système de coordonnées de la couche BRGM est inconnu.")

    limite_emprise = gpd.GeoSeries(
        [shape(emprise["geometry"])],
        crs="EPSG:4326",
    ).to_crs(geologie.crs).iloc[0]
    indices = geologie.sindex.query(limite_emprise, predicate="intersects")
    geologie = geologie.iloc[indices].copy()
    if geologie.empty:
        raise ValueError(
            "Aucune unité géologique BRGM trouvée dans la zone demandée."
        )

    geologie.geometry = geologie.geometry.intersection(limite_emprise)
    geologie = geologie[
        geologie.geometry.geom_type.isin(("Polygon", "MultiPolygon"))
    ].copy()
    if geologie.empty:
        raise ValueError(
            "Aucune formation géologique BRGM surfacique trouvée dans la zone demandée."
        )

    geologie = geologie.to_crs("EPSG:4326")
    geologie["NOTATION"] = geologie["NOTATION"].fillna(
        "Non renseignée"
    ).astype(str)
    geologie["DESCR"] = geologie["DESCR"].fillna(
        "Description non renseignée"
    ).astype(str)
    return json.loads(
        geologie[["NOTATION", "DESCR", "geometry"]].to_json(drop_id=True)
    )


def filtrer_lignes_emprise(lignes: gpd.GeoDataFrame, emprise: dict):
    """Conserve et découpe les lignes réseau dans l'emprise de recherche."""
    if lignes.empty:
        return lignes

    limite = gpd.GeoSeries(
        [shape(emprise["geometry"])],
        crs="EPSG:4326",
    ).to_crs("EPSG:2154").iloc[0]
    lignes = lignes.to_crs("EPSG:2154")
    indices = lignes.sindex.query(limite, predicate="intersects")
    lignes = lignes.iloc[indices].copy()
    lignes.geometry = lignes.geometry.intersection(limite)
    lignes = lignes[
        ~lignes.geometry.is_empty
        & lignes.geometry.geom_type.isin(("LineString", "MultiLineString"))
    ].copy()
    return lignes.to_crs("EPSG:4326")


@st.cache_data(ttl=3600, show_spinner=False)
def geocode_address(address: str):
    """Renvoie le code INSEE de la commune correspondant à l'adresse."""
    url = "https://api-adresse.data.gouv.fr/search/"
    response = requests.get(url, params={"q": address, "limit": 1}, timeout=20)
    response.raise_for_status()
    features = response.json().get("features", [])
    if not features:
        raise ValueError("Adresse introuvable. Essaie une adresse plus précise.")
    feature = features[0]
    lon, lat = feature["geometry"]["coordinates"]
    return feature["properties"].get("citycode"), lon, lat


@st.cache_data(ttl=3600, show_spinner=False)
def recuperer_donnees_grdf(commune: str):
    """Exporte les lignes GRDF d'une commune sans limite de pagination."""
    base_url = (
        "https://opendata.grdf.fr/api/explore/v2.1/catalog/datasets/"
        "cartographie-du-reseau-grdf-en-service"
    )

    response = requests.get(
        f"{base_url}/exports/json",
        params={
            "where": f"code_insee_administratif = '{commune}'",
            "select": "geo_shape,etat_serv,code_insee_administratif",
        },
        timeout=60,
    )
    response.raise_for_status()
    resultats = response.json()
    if not isinstance(resultats, list):
        raise ValueError("L'export GRDF n'a pas renvoyé une liste de lignes.")
    return resultats



def convertir_lignes(resultats):
    lignes = []

    for record in resultats:
        # Enedis expose "geometry", tandis que GRDF utilise "geo_shape".
        geometrie = record.get("geometry") or record.get("geo_shape")
        if not geometrie:
            continue

        if isinstance(geometrie, str):
            geometrie = json.loads(geometrie)

        lignes.append(
            {
                "etat_serv": record.get("etat_serv"),
                "geometry": shape(geometrie),
            }
        )

    return gpd.GeoDataFrame(
        lignes,
        columns=["etat_serv", "geometry"],
        geometry="geometry",
        crs="EPSG:4326",
    )

@st.cache_data(ttl=3600, show_spinner=False)
def tester_api_enedis(dataset: str, commune: str, emprise: dict):
    url = (
        "https://opendata.enedis.fr/data-fair/api/v1/"
        f"datasets/{dataset}/lines"
    )
    min_lon, min_lat, max_lon, max_lat = shape(emprise["geometry"]).bounds
    params = {
        "size": 1000,
        "qs": f"code_commune:{commune}",
        "bbox": f"{min_lon},{min_lat},{max_lon},{max_lat}",
    }

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    page = response.json()
    resultats = page.get("results", [])

    while page.get("next"):
        response = requests.get(page["next"], timeout=30)
        response.raise_for_status()
        page = response.json()
        resultats.extend(page.get("results", []))

    return resultats


def echantillonner_polyligne(
    coordinates: list,
    nombre_points_minimum: int = 10,
) -> tuple[float, list, list]:
    """Mesure une polyligne et génère des points réguliers pour son profil."""
    nombre_points_minimum = max(10, min(100, int(nombre_points_minimum)))
    points = []
    for coordinate in coordinates:
        if not isinstance(coordinate, (list, tuple)) or len(coordinate) < 2:
            raise ValueError("La polyligne contient des coordonnées invalides.")
        longitude, latitude = float(coordinate[0]), float(coordinate[1])
        if (
            not math.isfinite(longitude)
            or not math.isfinite(latitude)
            or not -180 <= longitude <= 180
            or not -90 <= latitude <= 90
        ):
            raise ValueError("La polyligne contient des coordonnées invalides.")
        points.append((longitude, latitude))

    if len(points) < 2:
        raise ValueError("Trace une polyligne composée d’au moins deux points.")

    segments = []
    for depart, arrivee in zip(points, points[1:]):
        azimut, _, longueur = GEOD_WGS84.inv(*depart, *arrivee)
        if longueur > 0:
            segments.append((depart, azimut, longueur))

    longueur_totale = sum(segment[2] for segment in segments)
    if longueur_totale <= 0:
        raise ValueError("La polyligne doit avoir une longueur supérieure à zéro.")

    nombre_points = min(
        100,
        max(nombre_points_minimum, math.ceil(longueur_totale / 25) + 1),
    )
    distances = [
        longueur_totale * index / (nombre_points - 1)
        for index in range(nombre_points)
    ]
    echantillons = []
    indice_segment = 0
    debut_segment = 0.0
    for distance in distances:
        while (
            indice_segment < len(segments) - 1
            and distance > debut_segment + segments[indice_segment][2]
        ):
            debut_segment += segments[indice_segment][2]
            indice_segment += 1
        depart, azimut, longueur = segments[indice_segment]
        longitude, latitude, _ = GEOD_WGS84.fwd(
            *depart,
            azimut,
            min(distance - debut_segment, longueur),
        )
        echantillons.append((longitude, latitude))

    return longueur_totale, distances, echantillons


@st.cache_data(ttl=86400, max_entries=64, show_spinner=False)
def recuperer_altitudes(echantillons: tuple) -> list:
    """Récupère les altitudes RGE ALTI de l’IGN aux points du profil."""
    response = requests.get(
        "https://data.geopf.fr/altimetrie/1.0/calcul/alti/rest/elevation.json",
        params={
            "lon": "|".join(f"{longitude:.6f}" for longitude, _ in echantillons),
            "lat": "|".join(f"{latitude:.6f}" for _, latitude in echantillons),
            "resource": "ign_rge_alti_wld",
            "delimiter": "|",
            "measures": "false",
            "zonly": "true",
            "indent": "false",
        },
        timeout=30,
    )
    response.raise_for_status()
    resultat = response.json()
    altitudes = resultat.get("elevations") if isinstance(resultat, dict) else None
    if not isinstance(altitudes, list) or len(altitudes) != len(echantillons):
        raise ValueError("Le service IGN n’a pas renvoyé toutes les altitudes.")
    if any(altitude is None for altitude in altitudes):
        raise ValueError("L’altitude est indisponible sur une partie du tracé.")
    try:
        altitudes = [float(altitude) for altitude in altitudes]
    except (TypeError, ValueError) as exc:
        raise ValueError("Le service IGN a renvoyé des altitudes invalides.") from exc
    if not all(math.isfinite(altitude) for altitude in altitudes):
        raise ValueError("Le service IGN a renvoyé des altitudes invalides.")
    return altitudes


def afficher_outil_profil_terrain(
    vue,
    lon: float,
    lat: float,
    gdf_carte: gpd.GeoDataFrame,
    couleurs_defaut: dict,
    reseaux_affiches: list,
    zoom_carte: float,
):
    """Affiche la carte de mesure et le profil altimétrique."""
    st.subheader("Mesurer une distance et voir le profil du terrain")
    st.caption(
        "Utilise l’outil polyligne en haut à gauche de la carte, puis clique "
        "sur le dernier point pour terminer le tracé."
    )
    colonne_carte, colonne_profil = st.columns((1.4, 1))
    with colonne_carte:
        carte_mesure = folium.Map(
            location=[vue.latitude, vue.longitude],
            zoom_start=min(18, max(12, round(zoom_carte))),
            control_scale=True,
        )
        folium.Marker(
            [lat, lon],
            tooltip="Adresse recherchée",
        ).add_to(carte_mesure)
        for reseau, couleur in couleurs_defaut.items():
            lignes_reseau = gdf_carte[
                (gdf_carte["reseau"] == reseau)
                & (gdf_carte["reseau"].isin(reseaux_affiches))
            ]
            if not lignes_reseau.empty:
                folium.GeoJson(
                    lignes_reseau.to_json(),
                    name=f"Réseau {reseau}",
                    style_function=lambda _, couleur=couleur: {
                        "color": couleur,
                        "weight": 3,
                    },
                    tooltip=folium.GeoJsonTooltip(
                        fields=["reseau"],
                        aliases=["Réseau :"],
                    ),
                ).add_to(carte_mesure)
        Draw(
            export=False,
            position="topleft",
            draw_options={
                "polyline": {
                    "allowIntersection": False,
                    "shapeOptions": {"color": "#e63946", "weight": 4},
                },
                "polygon": False,
                "rectangle": False,
                "circle": False,
                "circlemarker": False,
                "marker": False,
            },
            edit_options={"edit": False, "remove": True},
        ).add_to(carte_mesure)
        folium.LayerControl(collapsed=True).add_to(carte_mesure)
        resultat_mesure = st_folium(
            carte_mesure,
            key="carte_mesure_profil",
            height=540,
            returned_objects=["all_drawings"],
            use_container_width=True,
        )

    with colonne_profil:
        nombre_points_minimum = st.slider(
            "Nombre minimal de points du profil",
            min_value=10,
            max_value=100,
            value=10,
            step=10,
            help=(
                "Les tracés longs sont automatiquement échantillonnés plus "
                "densément, avec un maximum de 100 points."
            ),
        )
        if not isinstance(resultat_mesure, dict):
            st.error("La carte n’a pas renvoyé les données du tracé.")
            tracés = []
        else:
            tracés = resultat_mesure.get("all_drawings")
        tracés = tracés or []
        if not isinstance(tracés, list):
            st.error("La carte a renvoyé un tracé dans un format inattendu.")
            tracés = []
        polyligne = None
        for dessin in reversed(tracés):
            if not isinstance(dessin, dict):
                continue
            geometrie = dessin.get("geometry")
            if isinstance(geometrie, dict) and geometrie.get("type") == "LineString":
                polyligne = geometrie.get("coordinates")
                break
        if polyligne is not None:
            try:
                longueur, distances, echantillons = echantillonner_polyligne(
                    polyligne,
                    nombre_points_minimum,
                )
            except (TypeError, ValueError) as exc:
                st.error(f"Impossible de mesurer le tracé : {exc}")
            else:
                st.metric(
                    "Longueur du tracé",
                    (
                        f"{longueur:,.0f} m".replace(",", " ")
                        if longueur < 1000
                        else f"{longueur / 1000:,.2f} km".replace(",", " ")
                    ),
                )
                try:
                    with st.spinner("Calcul du profil altimétrique IGN…"):
                        altitudes = recuperer_altitudes(tuple(echantillons))
                except (requests.RequestException, ValueError) as exc:
                    st.error(f"Impossible de charger le profil altimétrique : {exc}")
                else:
                    denivele_positif = sum(
                        max(altitude_suivante - altitude, 0)
                        for altitude, altitude_suivante in zip(
                            altitudes, altitudes[1:]
                        )
                    )
                    st.metric(
                        "Dénivelé positif",
                        f"{denivele_positif:,.0f} m".replace(",", " "),
                    )
                    profil = pd.DataFrame(
                        {
                            "Distance (m)": distances,
                            "Altitude (m)": altitudes,
                        }
                    )
                    st.line_chart(
                        profil,
                        x="Distance (m)",
                        y="Altitude (m)",
                        alt="Profil altimétrique du tracé",
                    )
                    st.caption(
                        f"Profil indicatif issu du modèle RGE ALTI de l’IGN : "
                        f"{len(altitudes)} points, espacés d’environ "
                        f"{longueur / (len(altitudes) - 1):,.0f} m. "
                        "Échantillonnage automatique tous les 25 m environ, "
                        "avec un maximum de 100 points."
                    )
        else:
            st.info("Trace une polyligne sur la carte pour mesurer sa longueur.")









if __name__ == "__main__":
    st.title("Réseaux électriques Enedis et GRDF")
    st.sidebar.subheader("Rechercher une adresse")
    with st.sidebar.form("recherche_adresse"):
        adresse = st.text_input(
            "Adresse",
            value=st.session_state.get("adresse_recherchee", ""),
            placeholder="Ex. 10 rue de la Paix, Toulon",
        )
        lancer_recherche = st.form_submit_button(
            "Rechercher les réseaux",
            type="primary",
        )

    if lancer_recherche:
        if not adresse.strip():
            st.sidebar.error("Saisis une adresse avant de lancer la recherche.")
        else:
            try:
                with st.spinner("Recherche de l’adresse…"):
                    code_insee, longitude, latitude = geocode_address(adresse.strip())
            except (requests.RequestException, ValueError) as exc:
                st.sidebar.error(f"Impossible de trouver cette adresse : {exc}")
            else:
                if not code_insee:
                    st.sidebar.error(
                        "L’adresse a été trouvée, mais aucun code INSEE "
                        "de commune n’est disponible."
                    )
                else:
                    st.session_state["afficher_geologie_vectorielle"] = False
                    st.session_state["adresse_recherchee"] = adresse.strip()
                    st.session_state["code_insee"] = code_insee
                    st.session_state["longitude"] = longitude
                    st.session_state["latitude"] = latitude

    if "code_insee" not in st.session_state:
        st.info("Saisis une adresse dans la barre latérale pour afficher les réseaux.")
        st.stop()

    commune = st.session_state["code_insee"]
    lon = st.session_state["longitude"]
    lat = st.session_state["latitude"]
    emprise_reseaux = creer_emprise_carree_1km(lon, lat)
    dataset = "reseau-souterrain-hta"  # ou "reseau-souterrain-bt"
    dataset2 = "reseau-souterrain-bt"
    try:
        with st.spinner("Chargement des réseaux autour de l’adresse…"):
            resultats_hta = tester_api_enedis(
                dataset, commune=commune, emprise=emprise_reseaux
            )
            resultats_bt = tester_api_enedis(
                dataset2, commune=commune, emprise=emprise_reseaux
            )
            resultats_grdf = recuperer_donnees_grdf(commune=commune)
    except requests.RequestException as exc:
        st.error(f"Erreur lors du chargement des données réseau : {exc}")
        st.stop()
    df_lignes1 = convertir_lignes(resultats_hta)
    df_lignes2 = convertir_lignes(resultats_bt)
    df_lignes_3 = convertir_lignes(resultats_grdf)

    df_lignes1["reseau"] = "HTA"
    df_lignes2["reseau"] = "BT"
    df_lignes_3["reseau"] = "GRDF"

    df_lignes = pd.concat([df_lignes1, df_lignes2, df_lignes_3], ignore_index=True)

    gdf = filtrer_lignes_emprise(df_lignes, emprise_reseaux)

    total_hta = int((gdf["reseau"] == "HTA").sum())
    total_bt = int((gdf["reseau"] == "BT").sum())
    total_grdf = int((gdf["reseau"] == "GRDF").sum())

    st.caption("Comptages des tronçons dans le carré de 1 × 1 km autour de l’adresse.")
    st.caption(
        "Enedis est filtré par emprise avant téléchargement. "
        "L’export GRDF ne filtre pas à cette emprise : les lignes communales "
        "sont téléchargées puis découpées à la zone."
    )
    col_hta, col_bt, col_enedis, col_grdf = st.columns(4)
    col_hta.metric("Enedis HTA", f"{total_hta:,}".replace(",", " "))
    col_bt.metric("Enedis BT", f"{total_bt:,}".replace(",", " "))
    col_grdf.metric("GRDF", f"{total_grdf:,}".replace(",", " "))

    etats_grdf = gdf.loc[gdf["reseau"] == "GRDF", "etat_serv"].fillna(
        "Non renseigné"
    ).value_counts()
    resume_etats_grdf = ", ".join(
        f"{etat} : {nombre:,}".replace(",", " ")
        for etat, nombre in etats_grdf.items()
    )
    st.subheader("Informations sur les réseaux")
    st.markdown(
        f"""
        - **HTA** : réseau électrique souterrain moyenne tension. Les données Enedis
          consultées ne précisent ni son état (actif/hors service), ni sa classe de
          précision A/B/C.
        - **BT** : réseau électrique souterrain basse tension. L'état et la classe
          de précision A/B/C ne sont pas fournis dans les données Enedis.
        - **GRDF** : réseau de distribution de gaz. État indiqué par l'API :
          {resume_etats_grdf or "non renseigné"}.

        **Précision cartographique :** aucune classe A, B ou C n'est disponible
        dans les réponses utilisées. La carte ne permet donc pas de certifier la
        précision ou la position exacte des ouvrages.
        """
    )
    fibre_url = "https://cartefibre.arcep.fr/index.html?" + urlencode(
        {
            "lng": lon,
            "lat": lat,
            "zoom": 18,
            "mode": "normal",
            "legende": "true",
            "filter": "false",
            "trimestre": "2026T2",
        }
    )
    st.link_button(
        "Consulter la fibre à cette adresse sur la carte ARCEP",
        fibre_url,
    )
    st.caption(
        "La carte ARCEP ouvre les statuts fibre par adresse sans télécharger "
        "la base nationale complète dans l’application."
    )

    gdf_carte = gdf.explode(index_parts=False).reset_index(drop=True)
    gdf_carte["coordinates"] = gdf_carte.geometry.apply(
        lambda geom: list(geom.coords)
    )
    gdf_carte["info"] = "Réseau : " + gdf_carte["reseau"].astype(str)

    couleurs_defaut = {
        "HTA": "#FF5A00",
        "BT": "#0064FF",
        "GRDF": "#00FF00",
    }
    ligne_reseau = {
        "HTA": ":red[━━━]",
        "BT": ":blue[━━━]",
        "GRDF": ":green[━━━]",
    }
    reseaux_affiches = st.sidebar.pills(
        "Réseaux à afficher",
        options=list(couleurs_defaut),
        format_func=lambda reseau: f"{ligne_reseau[reseau]} {reseau}",
        selection_mode="multi",
        default=list(couleurs_defaut),
        key="reseaux_affiches",
    )

    styles_raster = {
        "Photos aériennes IGN": {
            "version": 8,
            "sources": {
                "fond": {
                    "type": "raster",
                    "tiles": [
                        "https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile"
                        "&VERSION=1.0.0&LAYER=ORTHOIMAGERY.ORTHOPHOTOS"
                        "&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/jpeg"
                        "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
                    ],
                    "tileSize": 256,
                    "attribution": "© IGN / Géoplateforme",
                }
            },
            "layers": [{"id": "fond", "type": "raster", "source": "fond"}],
        },
        "Satellite (Esri)": {
            "version": 8,
            "sources": {
                "fond": {
                    "type": "raster",
                    "tiles": [
                        "https://server.arcgisonline.com/ArcGIS/rest/services/"
                        "World_Imagery/MapServer/tile/{z}/{y}/{x}"
                    ],
                    "tileSize": 256,
                    "attribution": "Tiles © Esri",
                }
            },
            "layers": [{"id": "fond", "type": "raster", "source": "fond"}],
        },
        "Carte géologique BRGM (1/50 000)": {
            "version": 8,
            "sources": {
                "fond": {
                    "type": "raster",
                    "tiles": [
                        "https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile"
                        "&VERSION=1.0.0&LAYER=BRGM_SCAN_D_GEOL50"
                        "&STYLE=normal&TILEMATRIXSET=PM_0_15&FORMAT=image/jpeg"
                        "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}"
                    ],
                    "tileSize": 256,
                    "minzoom": 0,
                    "maxzoom": 15,
                    "attribution": "© BRGM / IGN - Géoplateforme",
                }
            },
            "layers": [{"id": "fond", "type": "raster", "source": "fond"}],
        },
    }
    styles_fond = {
        "Plan détaillé IGN": (
            "https://data.geopf.fr/annexes/ressources/vectorTiles/"
            "styles/PLAN.IGN/standard.json"
        ),
        **{
            nom: "data:application/json,"
            + quote(json.dumps(style, separators=(",", ":")))
            for nom, style in styles_raster.items()
        },
    }
    fond_carte = st.sidebar.selectbox("Fond de carte", list(styles_fond))

    st.sidebar.subheader("Couches cartographiques")
    afficher_geologie_vectorielle = st.sidebar.checkbox(
        "Géologie vectorisée BRGM (zone de 1 km²)",
        key="afficher_geologie_vectorielle",
    )
    st.sidebar.subheader("Aléas et canalisations (Géorisques)")
    afficher_argiles = st.sidebar.checkbox(
        "Exposition au retrait-gonflement des argiles",
        value=False,
    )
    afficher_canalisations = st.sidebar.checkbox(
        "Canalisations de transport (gaz, hydrocarbures, produits chimiques)",
        value=False,
    )
    couches_superposees = [
        (
            "Géologie vectorisée BRGM",
            "https://geoservices.brgm.fr/geologie",
            "SCAN_H_GEOL50",
            "default",
            afficher_geologie_vectorielle,
        ),
        (
            "Exposition aux argiles",
            "https://www.georisques.gouv.fr/services",
            "ALEARG_REALISE",
            "default",
            afficher_argiles,
        ),
        (
            "Canalisations de transport",
            "https://www.georisques.gouv.fr/services",
            "CANALISATIONS",
            "default",
            afficher_canalisations,
        ),
    ]
    afficher_au_moins_une_couche = any(
        couche[4] for couche in couches_superposees
    )
    opacite_couches = st.sidebar.slider(
        "Transparence des couches superposées",
        min_value=0,
        max_value=100,
        value=65,
        step=5,
        disabled=not afficher_au_moins_une_couche,
    )
    for nom, service_url, identifiant, style, afficher in couches_superposees:
        if afficher and identifiant != "SCAN_H_GEOL50":
            try:
                st.sidebar.markdown(f"**Légende : {nom}**")
                st.sidebar.image(
                    recuperer_legende_wms(service_url, identifiant, style),
                    alt=f"Légende cartographique : {nom}",
                )
            except (requests.RequestException, ValueError) as exc:
                st.sidebar.error(f"Impossible de charger la légende {nom} : {exc}")

    contour_commune = None
    min_lon = max_lon = lon
    min_lat = max_lat = lat
    try:
        contour_commune = recuperer_contour_commune(commune)
    except (requests.RequestException, ValueError) as exc:
        st.sidebar.error(f"Impossible de charger le contour communal : {exc}")

    min_lon, min_lat, max_lon, max_lat = shape(
        emprise_reseaux["geometry"]
    ).bounds
    emprise_geologie = emprise_reseaux
    couches = []
    if contour_commune:
        for nom, service_url, identifiant, _, afficher in couches_superposees:
            if not afficher:
                continue
            try:
                with st.spinner(f"Chargement de la couche : {nom}…"):
                    image, bornes = recuperer_image_wms_emprise(
                        service_url,
                        identifiant,
                        (
                            emprise_geologie
                            if identifiant == "SCAN_H_GEOL50"
                            else contour_commune
                        ),
                        taille_max=(
                            500 if identifiant == "SCAN_H_GEOL50" else 2200
                        ),
                    )
                couches.append(
                    pdk.Layer(
                        "BitmapLayer",
                        image=json.dumps(f"data:image/png;base64,{image}"),
                        bounds=bornes,
                        opacity=opacite_couches / 100,
                    )
                )
            except (requests.RequestException, ValueError) as exc:
                st.sidebar.error(f"Impossible de charger {nom} : {exc}")

        if afficher_geologie_vectorielle:
            try:
                with st.spinner(
                    "Chargement des unités géologiques BRGM interactives…"
                ):
                    geologie_interactive = recuperer_geologie_vectorielle_zone(
                        commune,
                        emprise_geologie,
                    )
                for feature in geologie_interactive["features"]:
                    proprietes = feature["properties"]
                    proprietes["info"] = (
                        f"Formation : {proprietes['DESCR']}\n"
                        f"Notation : {proprietes['NOTATION']}"
                    )
                couches.append(
                    pdk.Layer(
                        "GeoJsonLayer",
                        data=geologie_interactive,
                        filled=True,
                        stroked=False,
                        get_fill_color=[0, 0, 0, 1],
                        pickable=True,
                        auto_highlight=True,
                        highlight_color=[255, 255, 255, 80],
                    )
                )
            except (requests.RequestException, ValueError) as exc:
                st.sidebar.error(
                    "Impossible de charger les informations géologiques "
                    f"BRGM interactives : {exc}"
                )

        st.caption(
            "Les canalisations Géorisques concernent le transport de matières "
            "dangereuses, pas tous les réseaux locaux."
        )

    if contour_commune:
        couches.append(
            pdk.Layer(
                "GeoJsonLayer",
                data=contour_commune,
                stroked=True,
                filled=False,
                get_line_color=[95, 105, 110],
                get_line_width=1,
                line_width_min_pixels=1,
                pickable=False,
            )
        )

    if not gdf_carte.empty:
        couches.extend([
            pdk.Layer(
                "PathLayer",
                data=gdf_carte[gdf_carte["reseau"] == reseau],
                get_path="coordinates",
                get_color=[
                    int(couleurs_defaut[reseau][i:i + 2], 16)
                    for i in (1, 3, 5)
                ],
                get_width=1,
                width_min_pixels=1,
                pickable=True,
            )
            for reseau in couleurs_defaut
            if reseau in reseaux_affiches
            and not gdf_carte[gdf_carte["reseau"] == reseau].empty
        ])

    couches.append(
        pdk.Layer(
            "ScatterplotLayer",
            data=[{"longitude": lon, "latitude": lat}],
            get_position="[longitude, latitude]",
            get_fill_color=[220, 38, 38, 255],
            get_radius=8,
            radius_min_pixels=7,
            radius_max_pixels=12,
            stroked=True,
            get_line_color=[255, 255, 255, 255],
            line_width_min_pixels=2,
            pickable=False,
        )
    )

    centre_lat = (min_lat + max_lat) / 2
    largeur_degres = max(
        max_lon - min_lon,
        (max_lat - min_lat) / max(math.cos(math.radians(centre_lat)), 0.01),
        0.005,
    )
    zoom_carte = min(
        18,
        max(4, math.log2(360 * 800 / (512 * 0.7 * largeur_degres))),
    )
    vue = pdk.ViewState(
        latitude=centre_lat,
        longitude=(min_lon + max_lon) / 2,
        zoom=zoom_carte,
        pitch=0,
    )
    st.pydeck_chart(
        pdk.Deck(
            layers=couches,
            map_provider="maplibre",
            map_style=styles_fond[fond_carte],
            initial_view_state=vue,
            tooltip={"text": "{info}"},
        ),
        alt="Carte communale des réseaux, des risques et de l’adresse recherchée",
    )
    if "afficher_profil_terrain" not in st.session_state:
        st.session_state["afficher_profil_terrain"] = False
    if st.button(
        (
            "Masquer la carte de mesure et le profil terrain"
            if st.session_state["afficher_profil_terrain"]
            else "Activer la carte de mesure et le profil terrain"
        ),
        key="bouton_profil_terrain",
    ):
        st.session_state["afficher_profil_terrain"] = not st.session_state[
            "afficher_profil_terrain"
        ]
    if st.session_state["afficher_profil_terrain"]:
        afficher_outil_profil_terrain(
            vue,
            lon,
            lat,
            gdf_carte,
            couleurs_defaut,
            reseaux_affiches,
            zoom_carte,
        )
    if gdf_carte.empty:
        st.info("Aucun réseau n’est disponible pour cette commune.")