"""Carte des températures de cours d'eau français avec les données Hub'Eau.

Lancement : streamlit run Untitled-1.py
Dépendances : streamlit, requests, pandas, folium, streamlit-folium
"""

from datetime import date, timedelta

import folium
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import requests
import streamlit as st
from folium.plugins import MarkerCluster
from streamlit_folium import st_folium

API = "https://hubeau.eaufrance.fr/api/v1/temperature/chronique"
API_STATION ="https://hubeau.eaufrance.fr/api/v1/temperature/station"





params = {
    "size": 2000
}

response = requests.get(API, params=params, timeout=30)
response.raise_for_status()
payload = response.json()
records = payload.get("data", [])
df = pd.DataFrame(records)

response_station = requests.get(API_STATION, params=params, timeout=30)
response_station.raise_for_status()
payload_station = response_station.json()
records_station = payload_station.get("data", [])
df_station = pd.DataFrame(records_station)




# print(f"Nombre de mesures récupérées : {len(df)}")
# print("Colonnes disponibles :")
# print(df.columns.tolist())
# print(df.head())

# Export pour pouvoir examiner les données
df_station.to_csv("outputs\\stations_temperature.csv", index=False)
print(df_station)

# Conversion des champs en types exploitables
df["date_mesure_temp"] = pd.to_datetime(
    df["date_mesure_temp"],
    errors="coerce"
)

df["resultat"] = pd.to_numeric(
    df["resultat"],
    errors="coerce"
)

df = df.dropna(subset=["date_mesure_temp", "resultat"])
df["date_mesure_temp"] = df["date_mesure_temp"].dt.normalize()
df = df.groupby("date_mesure_temp", as_index=False)["resultat"].mean()
df = df.sort_values("date_mesure_temp")

df_station["latitude"] = pd.to_numeric(
    df_station["latitude"],
    errors="coerce"
)
df_station["longitude"] = pd.to_numeric(
    df_station["longitude"],
    errors="coerce"
)
df_station["coordonnee_x"] = pd.to_numeric(
    df_station["coordonnee_x"],
    errors="coerce"
)
df_station["coordonnee_y"] = pd.to_numeric(
    df_station["coordonnee_y"],
    errors="coerce"
)

df_station = df_station.dropna(subset=["latitude", "longitude"])





# points = []
# for ligne in enregistrements:
# 	latitude = champ(ligne, "latitude", "latitude_station", "lat")
# 	longitude = champ(ligne, "longitude", "longitude_station", "lon")
# 	temperature = champ(ligne, "resultat", "resultat_analyse", "valeur")
# 	try:
# 		latitude, longitude, temperature = float(latitude), float(longitude), float(temperature)
# 	except (TypeError, ValueError):
# 		continue
# 	# Afficher uniquement les coordonnées de France métropolitaine et valeurs plausibles.
# 	if 41 <= latitude <= 52 and -6 <= longitude <= 10 and -2 <= temperature <= 45:
# 		points.append({
# 			"lat": latitude,
# 			"lon": longitude,
# 			"temperature": temperature,
# 			"cours_eau": champ(ligne, "libelle_cours_eau", "nom_cours_eau") or "Cours d'eau non renseigné",
# 			"station": champ(ligne, "libelle_station", "nom_station", "code_station") or "Station",
# 			"date": champ(ligne, "date_mesure", "date_prelevement") or "Date non renseignée",
# 			"unite": champ(ligne, "libelle_unite", "unite") or "°C",
# 		})

# if not points:
# 	st.info("Aucune mesure géolocalisée exploitable n'a été renvoyée. Réessayez plus tard.")
# 	st.caption("Source : API Hub'Eau — qualité des cours d'eau, paramètre 1301 (température).")
# 	st.stop()

# donnees = pd.DataFrame(points)
# col1, col2 = st.columns(2)
# col1.metric("Mesures cartographiées", f"{len(donnees):,}".replace(",", " "))
# col2.metric("Température moyenne", f"{donnees['temperature'].mean():.1f} °C")

# carte = folium.Map(location=[46.6, 2.4], zoom_start=6, tiles="OpenStreetMap")
# marqueurs = MarkerCluster().add_to(carte)
# for point in points:
# 	couleur = "blue" if point["temperature"] < 15 else "orange" if point["temperature"] < 25 else "red"
# 	folium.CircleMarker(
# 		location=[point["lat"], point["lon"]],
# 		radius=6,
# 		color=couleur,
# 		fill=True,
# 		fill_opacity=0.8,
# 		tooltip=f"{point['cours_eau']} — {point['temperature']:g} {point['unite']}",
# 		popup=(f"Cours d'eau : {point['cours_eau']}<br>Station : {point['station']}<br>"
# 			   f"Température : {point['temperature']:g} {point['unite']}<br>Date : {point['date']}"),
# 	).add_to(marqueurs)

# st_folium(carte, use_container_width=True, height=650)
# with st.expander("Détails et limites"):
# 	st.write(
# 		"Chaque point correspond à une mesure en station, pas au tracé complet d'une rivière. "
# 		"Les données couvrent les trois dernières années et leur disponibilité dépend des stations."
# 	)
# 	st.markdown("Source : [Hub'Eau — qualité des cours d'eau](https://hubeau.eaufrance.fr/page/api-qualite-cours-deau)")
