import requests
import json
import geopandas as gpd
import streamlit as st
import pydeck as pdk
from shapely.geometry import shape
import pandas as pd


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


def recuperer_donnees_grdf(taille, commune=None, lon=None, lat=None):
    """Récupère des enregistrements du dataset GRDF en service."""
    GRDF_API = ( "https://opendata.grdf.fr/api/explore/v2.1/catalog/datasets/" 
                "cartographie-du-reseau-grdf-en-service/records"
    )

    params = {
           "size": taille,
           "qs": f"code_insee_administratif:{commune}" if commune else None}

    response = requests.get(GRDF_API, params=params, timeout=30)
    response.raise_for_status()
    return response.json()



def convertir_lignes(resultats):
    lignes = []

    for record in resultats:
        geometrie = record.get("geometry")
        if not geometrie:
            continue

        if isinstance(geometrie, str):
            geometrie = json.loads(geometrie)

        lignes.append({
            "commune": record.get("nom_commune"),
            "departement": record.get("nom_departement"),
            "geometry": shape(geometrie),
        })

    return gpd.GeoDataFrame(
        lignes,
        geometry="geometry",
        crs="EPSG:4326",
    )

def tester_api_enedis(dataset, taille=1000, commune=None):
    url = (
        "https://opendata.enedis.fr/data-fair/api/v1/"
        f"datasets/{dataset}/lines"
    )

    params = {
       "size": taille,
       "qs": f"code_commune:{commune}" if commune else None}

    #print("Nombre de résultats :", data["total"])
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()

    return data

if __name__ == "__main__":
    dataset = "reseau-souterrain-hta"  # ou "reseau-souterrain-bt"
    dataset2 = "reseau-souterrain-bt"
    commune, lon, lat = geocode_address("285 rue du Dr Laennec, France")

    donnees = tester_api_enedis(dataset, commune=commune)
    donnees2 = tester_api_enedis(dataset2, commune=commune)
    donnees_grdf = recuperer_donnees_grdf(taille=200, commune=commune, lon=lon, lat=lat)
    #print("#######")
    print(donnees_grdf["results"])
    df_lignes1 = convertir_lignes(donnees["results"])
    df_lignes2 = convertir_lignes(donnees2["results"])
    df_lignes_3 = convertir_lignes(donnees_grdf["results"])

    df_lignes1["reseau"] = "HTA"
    df_lignes2["reseau"] = "BT"
    df_lignes_3["reseau"] = "GRDF"
    df_lignes = pd.concat([df_lignes1, df_lignes2, df_lignes_3], ignore_index=True)
    print(df_lignes)

    donnees_grdf = recuperer_donnees_grdf(
        taille=20, commune=commune, lon=lon, lat=lat
    )
    resultats_grdf = donnees_grdf.get("results", [])
    print("Total d'enregistrements GRDF :", donnees_grdf.get("total_count"))
    print("Premiers enregistrements GRDF :", resultats_grdf[:3])
    #df_lignes.explore()

    code_commune = geocode_address("Toulon, France")
    print(f"Code INSEE de la commune : {code_commune}")


    st.title("Réseaux électriques Enedis")
    st.subheader("Réseau GRDF en service")
    st.write(f"Total d'enregistrements : {donnees_grdf.get('total_count', 'inconnu')}")
    if resultats_grdf:
        st.dataframe(pd.json_normalize(resultats_grdf), use_container_width=True)

    # gdf contient les lignes récupérées depuis l'API
    # Vérifier que les coordonnées sont en longitude / latitude
    gdf = df_lignes.to_crs(epsg=4326)

    # Limiter le volume pour tester
    gdf_carte = gdf.head(1000).copy()

    # Pydeck attend des coordonnées sous forme de listes
    gdf_carte["coordinates"] = gdf_carte.geometry.apply(
        lambda geom: list(geom.coords) if geom.geom_type == "LineString" else []
    )

    couleurs_reseau = {"HTA": [255, 90, 0], "BT": [0, 100, 255], "GRDF": [0, 255, 0]}
    gdf_carte["couleur"] = gdf_carte["reseau"].map(couleurs_reseau)

    gdf_carte = gdf_carte[gdf_carte["coordinates"].map(len) > 1]

    if not gdf_carte.empty:
        couche = pdk.Layer(
            "PathLayer",
            data=gdf_carte,
            get_path="coordinates",
            get_color="couleur",
            get_width=1,
            width_min_pixels=1,
            pickable=True,
        )

        vue = pdk.ViewState(
            latitude=lat,
            longitude=lon,
            zoom=12,
            pitch=0,
        )

        st.pydeck_chart(
            pdk.Deck(
                layers=[couche],
                initial_view_state=vue,
                tooltip={"text": "Réseau Enedis HTA, BT et GRDF"},
            )
        )
    else:
        st.warning("Aucune ligne exploitable dans les données.")