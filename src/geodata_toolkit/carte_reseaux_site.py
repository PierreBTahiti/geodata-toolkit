"""
Carte indicative des réseaux autour d'un site
---------------------------------------------
Sources :
- Géocodage adresse : API Adresse (BAN)
- Gaz GRDF : API Open Data GRDF / Opendatasoft
- Enedis BT/HTA et RTE : fichiers vectoriels téléchargés manuellement
  depuis les portails officiels puis filtrés localement.
"""

from pathlib import Path
import requests
import pandas as pd
import geopandas as gpd
import folium
import streamlit as st
from shapely.geometry import Point, box
from streamlit_folium import st_folium

# ----------------------- CONFIGURATION -----------------------
OUT_DIR = Path("data_reseaux")
OUT_DIR.mkdir(exist_ok=True)

# Déposer ici les fichiers téléchargés depuis les portails officiels.
# Pour un Shapefile, garder tous les fichiers associés (.shp, .shx, .dbf, .prj)
LOCAL_FILES = {
    "Transport électrique RTE": OUT_DIR / "rte_souterrain.shp",
    # Exemple GeoPackage : OUT_DIR / "reseaux_locaux.gpkg"
}

GRDF_API = (
    "https://opendata.grdf.fr/api/explore/v2.1/catalog/datasets/"
    "cartographie-du-reseau-grdf-en-service/records"
)

COLORS = {
    "Gaz GRDF": "#e67e22",
    "Électricité Enedis BT": "#2980b9",
    "Électricité Enedis HTA": "#8e44ad",
    "Transport électrique RTE": "#c0392b",
    "Autre réseau": "#16a085",
}


def geocode_address(address: str):
    """Géocode une adresse avec l'API officielle Adresse (BAN)."""
    url = "https://api-adresse.data.gouv.fr/search/"
    response = requests.get(url, params={"q": address, "limit": 1}, timeout=20)
    response.raise_for_status()
    features = response.json().get("features", [])
    if not features:
        raise ValueError("Adresse introuvable. Essaie une adresse plus précise.")
    feature = features[0]
    lon, lat = feature["geometry"]["coordinates"]
    label = feature["properties"].get("label", address)
    return lon, lat, label


def fetch_grdf(lon: float, lat: float, radius_m: int):
    """
    Récupère les conduites GRDF dans une emprise autour du site.
    On interroge l'API par emprise puis on filtre réellement par distance.
    L'API peut limiter le nombre de résultats ; le nombre retourné est affiché.
    """
    # Approximation en degrés, suffisante pour définir une emprise de recherche.
    delta_lat = radius_m / 111_000
    delta_lon = radius_m / (111_000 * max(abs(__import__("math").cos(
        __import__("math").radians(lat))), 0.2))
    lon_min, lat_min = lon - delta_lon, lat - delta_lat
    lon_max, lat_max = lon + delta_lon, lat + delta_lat

    params = {
        "limit": 100,
        "where": (
            f"within_bbox(geo_shape, {lon_min}, {lat_min}, "
            f"{lon_max}, {lat_max})"
        ),
    }
    features = []
    offset = 0
    page_size = 100
    while True:
        params["limit"] = page_size
        params["offset"] = offset
        response = requests.get(GRDF_API, params=params, timeout=45)
        response.raise_for_status()
        payload = response.json()
        records = payload.get("results", [])
        if not records:
            break
        for record in records:
            geom = record.get("geo_shape")
            if not geom:
                continue
            # API renvoie généralement GeoJSON dans geo_shape
            try:
                features.append({
                    "geometry": geom,
                    "properties": {
                        k: v for k, v in record.items() if k != "geo_shape"
                    },
                })
            except Exception:
                continue
        if len(records) < page_size:
            break
        offset += page_size
        # Garde-fou pour éviter de télécharger une emprise trop large.
        if offset >= 5000:
            break

    if not features:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

    rows = []
    for item in features:
        try:
            from shapely.geometry import shape
            rows.append({**item["properties"], "geometry": shape(item["geometry"])})
        except Exception:
            pass
    if not rows:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")

    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    # Filtre exact par distance en projection métrique
    metric = gdf.to_crs(2154)
    site = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(2154).iloc[0]
    metric = metric[metric.distance(site) <= radius_m]
    return metric.to_crs(4326)


def load_local_networks(lon: float, lat: float, radius_m: int):
    """Charge et filtre les fichiers vectoriels locaux autour du site."""
    layers = {}
    site_metric = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(2154).iloc[0]
    area_metric = site_metric.buffer(radius_m)

    for name, path in LOCAL_FILES.items():
        if not path.exists():
            continue
        try:
            gdf = gpd.read_file(path)
            if gdf.empty:
                continue
            if gdf.crs is None:
                st.warning(f"{name} : système de coordonnées absent. Couche ignorée.")
                continue
            gdf = gdf.to_crs(2154)
            # Filtre spatial par intersection du tampon de recherche
            gdf = gdf[gdf.intersects(area_metric)]
            if not gdf.empty:
                layers[name] = gdf.to_crs(4326)
        except Exception as exc:
            st.warning(f"Impossible de lire {path.name} ({name}) : {exc}")
    return layers


def add_gdf_to_map(map_obj, gdf, name, color):
    """Ajoute une couche vectorielle à Folium avec infobulles."""
    if gdf.empty:
        return
    # Limiter les propriétés dans les popups pour éviter des fiches énormes.
    geojson = gdf.to_json(drop_id=True)
    folium.GeoJson(
        geojson,
        name=name,
        style_function=lambda feature, c=color: {
            "color": c, "weight": 3, "opacity": 0.85
        },
        tooltip=folium.GeoJsonTooltip(
            fields=[c for c in gdf.columns if c != "geometry"][:5],
            aliases=[c.replace("_", " ").title() + ":"
                     for c in gdf.columns if c != "geometry"][:5],
            sticky=True,
        ) if any(c != "geometry" for c in gdf.columns) else None,
    ).add_to(map_obj)


# ----------------------- STREAMLIT UI -----------------------
st.set_page_config(page_title="Réseaux enterrés — repérage indicatif", layout="wide")
st.title("Repérage indicatif des réseaux autour d'un site")
st.warning(
    "Carte de préparation d'étude uniquement. Les tracés peuvent être incomplets "
    "ou imprécis : ne pas les utiliser pour décider où forer, sonder ou terrasser. "
    "Les procédures réglementaires et la détection terrain restent nécessaires."
)

with st.sidebar:
    st.header("Site à étudier")
    address = st.text_input(
        "Adresse, commune ou lieu-dit",
        value="Sanary-sur-Mer, France",
    )
    radius = st.slider("Rayon de recherche (m)", 100, 2000, 500, step=100)
    show_grdf = st.checkbox("Interroger l'API GRDF (gaz)", value=True)
    run = st.button("Rechercher et afficher", type="primary")

st.markdown(
    "Les fichiers Enedis/RTE doivent être téléchargés depuis les portails officiels "
    "et placés dans le dossier `data_reseaux` avec les noms indiqués dans le script."
)

if run:
    try:
        lon, lat, label = geocode_address(address)
        st.success(f"Site géocodé : {label} ({lat:.6f}, {lon:.6f})")
    except Exception as exc:
        st.error(f"Échec du géocodage : {exc}")
        st.stop()

    site = Point(lon, lat)
    layers = load_local_networks(lon, lat, radius)

    if show_grdf:
        with st.spinner("Recherche des conduites GRDF..."):
            try:
                grdf = fetch_grdf(lon, lat, radius)
                if not grdf.empty:
                    layers["Gaz GRDF"] = grdf
                else:
                    st.info("Aucune conduite GRDF retournée dans le rayon demandé.")
            except Exception as exc:
                st.warning(
                    "L'API GRDF n'a pas répondu comme prévu. "
                    "Tu peux télécharger le jeu GRDF manuellement et l'ajouter "
                    f"comme couche locale. Détail technique : {exc}"
                )

    map_obj = folium.Map(location=[lat, lon], zoom_start=17, control_scale=True)
    folium.Marker(
        [lat, lon],
        tooltip="Site étudié",
        popup=label,
        icon=folium.Icon(color="green", icon="flag"),
    ).add_to(map_obj)

    folium.Circle(
        [lat, lon],
        radius=radius,
        color="#555555",
        fill=False,
        dash_array="5, 8",
        tooltip=f"Rayon de recherche : {radius} m",
    ).add_to(map_obj)

    counts = []
    for name, gdf in layers.items():
        color = COLORS.get(name, COLORS["Autre réseau"])
        add_gdf_to_map(map_obj, gdf, name, color)
        counts.append({"Réseau": name, "Objets affichés": len(gdf)})

    folium.LayerControl(collapsed=False).add_to(map_obj)
    st_folium(map_obj, use_container_width=True, height=650)

    if counts:
        st.subheader("Résultats")
        st.dataframe(pd.DataFrame(counts), use_container_width=True, hide_index=True)
    else:
        st.info(
            "Aucun objet récupéré. Vérifie l'adresse, le rayon, la disponibilité "
            "de l'API et les fichiers locaux."
        )

    # Export des couches réunies en GeoPackage et GeoJSON
    export_layers = []
    for name, gdf in layers.items():
        if gdf.empty:
            continue
        tmp = gdf.copy()
        tmp["type_reseau"] = name
        export_layers.append(tmp)

    if export_layers:
        combined = gpd.GeoDataFrame(
            pd.concat(export_layers, ignore_index=True),
            geometry="geometry",
            crs="EPSG:4326",
        )
        gpkg_path = OUT_DIR / "reseaux_autour_site.gpkg"
        geojson_path = OUT_DIR / "reseaux_autour_site.geojson"
        try:
            combined.to_file(gpkg_path, layer="reseaux", driver="GPKG")
            combined.to_file(geojson_path, driver="GeoJSON")
            st.success(f"Exports créés : `{gpkg_path}` et `{geojson_path}`")
            st.caption("Les fichiers sont enregistrés localement dans le dossier du projet.")
        except Exception as exc:
            st.warning(f"Export impossible : {exc}")
