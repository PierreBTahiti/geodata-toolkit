import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt

st.title("🌊 Température des cours d'eau")
st.write("Exploration des données Hub'Eau")

df = pd.read_csv("outputs/mesures_temperature.csv")

st.subheader("Données récupérées")
st.dataframe(df)

st.subheader("Évolution de la température")

# À adapter aux noms réels des colonnes
df["date_mesure_temp"] = pd.to_datetime(
    df["date_mesure"], errors="coerce"
)
df["resultat"] = pd.to_numeric(
    df["resultat"], errors="coerce"
)

df = df.dropna(subset=["date_mesure_temp", "resultat"])

fig, ax = plt.subplots(figsize=(10, 4))
ax.plot(df["date_mesure"], df["resultat"])
ax.set_xlabel("Date")
ax.set_ylabel("Température (°C)")
ax.grid(True, alpha=0.3)

st.pyplot(fig)