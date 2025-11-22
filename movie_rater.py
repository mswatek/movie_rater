import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import random

# --- Google Sheets Setup ---
scopes = ["https://www.googleapis.com/auth/spreadsheets"]
skey = st.secrets["gcp_service_account"]
credentials = Credentials.from_service_account_info(skey, scopes=scopes)
client = gspread.authorize(credentials)
url = st.secrets["private_gsheets_url"]
sheet_name = "Movies"   # 👈 your Movies sheet
worksheet = client.open_by_url(url).worksheet(sheet_name)

# --- Load and Clean Data ---
def load_movies():
    data = worksheet.get_all_records()

    if not data:
        # Initialize with Elo column if sheet is empty
        df_movies = pd.DataFrame(columns=[
            "Released","Date_Viewed","Year_Viewed","Title","tconst","Platform",
            "Rewatch","Type","genres","rating","votes","runtime","director","poster_url","elo"
        ])
        df_movies["elo"] = 1500
        worksheet.update([df_movies.columns.values.tolist()] + df_movies.values.tolist())
        return df_movies

    df_movies = pd.DataFrame(data)

    # ✅ Ensure required columns exist
    if "elo" not in df_movies.columns:
        df_movies["elo"] = 1500

    # ✅ Cast string fields
    df_movies["Title"] = df_movies["Title"].astype(str).str.strip()
    df_movies["director"] = df_movies["director"].astype(str).str.strip()
    df_movies["poster_url"] = df_movies.get("poster_url", "").astype(str).str.strip()
    df_movies["genres"] = df_movies["genres"].astype(str).str.strip()

    # ✅ Ensure elo is numeric
    df_movies["elo"] = pd.to_numeric(df_movies["elo"], errors="coerce").fillna(1500).astype(int)

    return df_movies

df_movies = load_movies()

# --- Helper: Safe Image Display ---
def safe_image(url, caption=None):
    if isinstance(url, str) and url.strip().startswith("http"):
        st.image(url.strip(), width="stretch", caption=caption)
    else:
        st.write("🎞️ No poster available")
        if caption:
            st.write(caption)

# --- Helper: Genre Overlap ---
def get_genre_set(genres_str):
    if isinstance(genres_str, str):
        return set(g.strip().lower() for g in genres_str.split(",") if g.strip())
    return set()

def sample_movie_pair(df):
    # Try up to 50 times to find a valid pair
    for _ in range(50):
        pair = df.sample(2).reset_index(drop=True)
        g1 = get_genre_set(pair.iloc[0]["genres"])
        g2 = get_genre_set(pair.iloc[1]["genres"])
        if g1 & g2:  # non-empty intersection
            return pair
    # Fallback: just return any two
    return df.sample(2).reset_index(drop=True)

# --- App UI ---
st.title("🎬 Movie Rater")
st.write("Choose your favorite between two movies. Elo scores will update based on your vote.")

# --- Random Movie Pair ---
if "movie_pair" not in st.session_state:
    st.session_state.movie_pair = sample_movie_pair(df_movies)

movie1, movie2 = st.session_state.movie_pair.iloc[0], st.session_state.movie_pair.iloc[1]

# --- Show Shared Genres Above ---
shared_genres = get_genre_set(movie1["genres"]) & get_genre_set(movie2["genres"])
if shared_genres:
    st.write(f"🎭 These two are being compared in the **{', '.join(sorted(shared_genres)).title()}** genre(s).")
else:
    st.write("🎭 These two movies don’t share a genre (fallback pairing).")

# --- Voting Buttons ---
if "vote" not in st.session_state:
    st.session_state.vote = None

col1, col2 = st.columns(2)

with col1:
    safe_image(movie1["poster_url"], f"{movie1['Title'].title()} (Dir. {movie1['director']})")
    if st.button(f"Vote: {movie1['Title'].title()}"):
        st.session_state.vote = (movie1, movie2)

with col2:
    safe_image(movie2["poster_url"], f"{movie2['Title'].title()} (Dir. {movie2['director']})")
    if st.button(f"Vote: {movie2['Title'].title()}"):
        st.session_state.vote = (movie2, movie1)

# --- Skip Button ---
if st.button("🔄 Skip this pair"):
    st.session_state.movie_pair = sample_movie_pair(df_movies)
    st.rerun()

# --- Elo Update Function ---
def update_elo(winner_elo, loser_elo, k=32):
    expected_win = 1 / (1 + 10 ** ((loser_elo - winner_elo) / 400))
    new_winner_elo = winner_elo + k * (1 - expected_win)
    new_loser_elo = loser_elo - k * (1 - expected_win)
    return round(new_winner_elo), round(new_loser_elo)

# --- Process Vote and Update Sheet ---
if st.session_state.vote:
    winner, loser = st.session_state.vote
    new_winner_elo, new_loser_elo = update_elo(winner["elo"], loser["elo"])

    # ✅ Update Elo for all rows with same Title
    df_movies.loc[df_movies["Title"].str.lower() == winner["Title"].lower(), "elo"] = new_winner_elo
    df_movies.loc[df_movies["Title"].str.lower() == loser["Title"].lower(), "elo"] = new_loser_elo

    sheet_data = worksheet.get_all_values()
    headers = [h.strip().lower() for h in sheet_data[0]]
    title_idx = headers.index("title")
    elo_idx = headers.index("elo")

    try:
        # Update all rows in sheet with same Title
        for i, row in enumerate(sheet_data[1:], start=2):
            row_title = row[title_idx].strip().lower()
            if row_title == winner["Title"].strip().lower():
                worksheet.update_cell(i, elo_idx + 1, new_winner_elo)
            if row_title == loser["Title"].strip().lower():
                worksheet.update_cell(i, elo_idx + 1, new_loser_elo)

        st.success(f"You voted for **{winner['Title'].title()}**! Elo updated.")
    except Exception as e:
        st.error(f"❌ Update failed: {e}")

    # Reset and rerun
    st.session_state.vote = None
    st.session_state.movie_pair = sample_movie_pair(df_movies)
    st.rerun()

# --- Leaderboard ---
st.subheader("🏆 Leaderboard")

# Create genre filter options
all_genres = set()
for g in df_movies["genres"].dropna():
    for genre in g.split(","):
        genre = genre.strip()
        if genre:
            all_genres.add(genre)

genre_options = ["Overall"] + sorted(all_genres)
selected_genre = st.selectbox("Filter leaderboard by genre:", genre_options)

# ✅ Aggregate leaderboard by Title so duplicates collapse
if selected_genre == "Overall":
    leaderboard_df = (
        df_movies.groupby("Title", as_index=False)
        .first()[["Title","director","genres","elo"]]
        .sort_values("elo", ascending=False)
    )
else:
    mask = df_movies["genres"].str.contains(selected_genre, case=False, na=False)
    leaderboard_df = (
        df_movies[mask]
        .groupby("Title", as_index=False)
        .first()[["Title","director","genres","elo"]]
        .sort_values("elo", ascending=False)
    )

st.dataframe(leaderboard_df, use_container_width=True, hide_index=True)

# --- Top Movie in Each Genre ---
def get_top_movies_by_genre(df):
    top_movies = []
    for g in sorted(set(genre.strip() for gs in df["genres"].dropna() for genre in gs.split(","))):
        mask = df["genres"].str.contains(g, case=False, na=False)
        genre_df = (
            df[mask]
            .groupby("Title", as_index=False)
            .first()
            .sort_values("elo", ascending=False)
        )
        if not genre_df.empty:
            top_movies.append((g, genre_df.iloc[0]))
    return top_movies


st.subheader("🎞️ Top Movie in Each Genre")

top_movies = get_top_movies_by_genre(df_movies)

# Build a horizontal scroll container
scroll_html = "<div style='display:flex; overflow-x:auto; gap:20px; padding:10px;'>"

for genre, movie in top_movies:
    poster = movie["poster_url"]
    title = movie["Title"]
    director = movie["director"]
    elo = movie["elo"]

    if poster and poster.startswith("http"):
        scroll_html += (
            f"<div style='flex:0 0 auto; text-align:center;'>"
            f"<img src='{poster}' style='height:250px; border-radius:8px;'>"
            f"<div><b>{title}</b><br>({genre})<br>Dir. {director}<br>Elo: {elo}</div>"
            f"</div>"
        )

scroll_html += "</div>"

# ✅ Render as HTML
st.markdown(scroll_html, unsafe_allow_html=True)
