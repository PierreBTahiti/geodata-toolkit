import requests
import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import time

# with st.spinner("Chargement des mesures…",show_spinner=True):
#     time.sleep(1)
st.title("🌊 Température des cours d'eau et position des mesures")
st.write("Exploration des données Hub'Eau")


df = pd.read_csv("outputs/mesures_temperature.csv")
df_station = pd.read_csv("outputs/stations_temperature.csv")

st.subheader("Données récupérées")
st.dataframe(df_station)

st.subheader("Évolution de la température")

# À adapter aux noms réels des colonnes
df["date_mesure_temp"] = pd.to_datetime(
    df["date_mesure_temp"], errors="coerce"
)
df["resultat"] = pd.to_numeric(
    df["resultat"], errors="coerce"
)

df_station["latitude"] = pd.to_numeric(
    df_station["latitude"],
    errors="coerce"
)
df_station["longitude"] = pd.to_numeric(
    df_station["longitude"],
    errors="coerce"
)

df = df.dropna(subset=["date_mesure_temp", "resultat"])
df["date_mesure_temp"] = df["date_mesure_temp"].dt.normalize()
df = df.groupby("date_mesure_temp", as_index=False)["resultat"].mean()
df = df.sort_values("date_mesure_temp")

fig, ax = plt.subplots(figsize=(10, 4))
ax.plot(df["date_mesure_temp"], df["resultat"])
ax.set_xlabel("Date")
ax.set_ylabel("Température (°C)")
ax.grid(True, alpha=0.3)

df_station = df_station.dropna(subset=["latitude", "longitude"])
st.subheader("Carte des stations de mesure")
st.write(f"Nombre de stations : {len(df_station)}")
st.map(df_station[["latitude", "longitude"]].dropna())

st.pyplot(fig)

