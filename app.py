import os
from pathlib import Path
from urllib.parse import quote
import json
import math

# Atur PROJ hanya jika folder Anaconda tersedia (untuk laptop Windows).
PROJ_PATH = r"C:\anaconda\envs\geospasial\Library\share\proj"
if os.path.isdir(PROJ_PATH):
    os.environ.setdefault("PROJ_LIB", PROJ_PATH)
    os.environ.setdefault("PROJ_DATA", PROJ_PATH)

import streamlit as st
import geopandas as gpd
import folium
import osmnx as ox
import networkx as nx
from shapely.geometry import Point
from streamlit_folium import st_folium
from folium.plugins import MarkerCluster

st.set_page_config(
    page_title="GeoHealth Surabaya",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data_geohealth"
CRS_METER = "EPSG:32749"

st.markdown("""
<style>
.block-container {padding-top: 1.1rem; padding-bottom: 2rem; max-width: 1500px;}
[data-testid="stMetric"] {background: #f4f7fb; border: 1px solid #e5ebf2;
padding: 13px 15px; border-radius: 13px;}
div.stButton > button {border-radius: 10px; font-weight: 600; min-height: 2.6rem;}
.small-note {color:#667085;font-size:0.85rem;}
</style>
""", unsafe_allow_html=True)

@st.cache_data(show_spinner="Memuat data spasial...")
def load_data():
    boundary_path = DATA_DIR / "batas_surabaya.geojson"
    facilities_path = DATA_DIR / "faskes_bersih.geojson"
    if not boundary_path.exists() or not facilities_path.exists():
        raise FileNotFoundError("Pastikan batas_surabaya.geojson dan faskes_bersih.geojson ada di folder data_geohealth.")
    boundary = gpd.read_file(boundary_path).to_crs("EPSG:4326")
    facilities = gpd.read_file(facilities_path).to_crs("EPSG:4326")
    facilities = facilities.reset_index(drop=True)
    facilities = facilities[facilities.geometry.notna() & ~facilities.geometry.is_empty].copy()
    facilities["geometry"] = facilities.geometry.representative_point()
    for col in ["name", "jenis_faskes", "website", "phone", "operator", "opening_hours", "addr:street", "addr:full"]:
        if col not in facilities.columns:
            facilities[col] = None
    facilities["name"] = facilities["name"].fillna("Fasilitas tanpa nama")
    return boundary, facilities

@st.cache_data(show_spinner=False)
def projected_facilities(facilities_json):
    gdf = gpd.read_file(facilities_json).to_crs(CRS_METER)
    return gdf

@st.cache_resource(show_spinner="Menyiapkan jaringan jalan...")
def load_graph():
    graph_path = DATA_DIR / "jaringan_jalan_dengan_waktu.graphml"
    if not graph_path.exists():
        raise FileNotFoundError("File jaringan_jalan_dengan_waktu.graphml tidak ditemukan.")
    graph = ox.load_graphml(graph_path)
    # Pastikan bobot travel_time numerik dan tersedia.
    for _, _, _, data in graph.edges(keys=True, data=True):
        if "travel_time" not in data:
            length = float(data.get("length", 0))
            speed = float(data.get("speed_kph", 25))
            data["travel_time"] = length / max(speed * 1000 / 3600, 0.1)
        else:
            data["travel_time"] = float(data["travel_time"])
    return graph

def safe_text(value, fallback="Belum tersedia"):
    if value is None:
        return fallback
    try:
        if str(value).strip().lower() in ("", "nan", "none"):
            return fallback
    except Exception:
        return fallback
    return str(value)

def google_maps_search(name, lat, lon):
    query = quote(f"{name} Surabaya")
    return f"https://www.google.com/maps/search/?api=1&query={query}&query_place_id="

def make_map(boundary, facilities, selected_types, show_radius, show_boundary):
    city = boundary.geometry.union_all()
    minx, miny, maxx, maxy = city.bounds
    center = [(miny + maxy) / 2, (minx + maxx) / 2]
    m = folium.Map(location=center, zoom_start=11, tiles=None, control_scale=True)
    folium.TileLayer("OpenStreetMap", name="Peta jalan", control=True).add_to(m)
    folium.TileLayer(
        tiles="CartoDB positron", name="Peta terang", control=True
    ).add_to(m)

    if show_boundary:
        folium.GeoJson(
            boundary.to_json(),
            name="Batas Kota Surabaya",
            style_function=lambda f: {"color": "#1769aa", "weight": 3, "fillOpacity": 0.025},
            tooltip="Batas Kota Surabaya",
        ).add_to(m)

    colors = {
        "Rumah Sakit": "red", "Puskesmas": "green",
        "Klinik": "blue", "Praktik Dokter": "orange",
        "Fasilitas Kesehatan Lainnya": "purple",
    }
    subset = facilities[facilities["jenis_faskes"].isin(selected_types)]
    cluster = MarkerCluster(name="Fasilitas kesehatan", disableClusteringAtZoom=15).add_to(m)

    for _, row in subset.iterrows():
        pt = row.geometry
        name = safe_text(row.get("name"), "Fasilitas tanpa nama")
        kind = safe_text(row.get("jenis_faskes"))
        address = safe_text(row.get("addr:full"), safe_text(row.get("addr:street")))
        popup_html = (
            f"<b>{name}</b><br>{kind}<br>{address}"
            f"<br><a href='https://www.google.com/maps/search/?api=1&query={pt.y}%2C{pt.x}' target='_blank'>Buka di Google Maps</a>"
        )
        folium.Marker(
            [pt.y, pt.x],
            tooltip=f"{name} · {kind}",
            popup=folium.Popup(popup_html, max_width=280),
            icon=folium.Icon(color=colors.get(kind, "gray"), icon="plus-sign", prefix="glyphicon"),
        ).add_to(cluster)

    if show_radius:
        # Radius ilustratif: bukan standar resmi dan bukan jangkauan berbasis jaringan jalan.
        main = facilities[facilities["jenis_faskes"].isin(["Rumah Sakit", "Puskesmas"])]
        for _, row in main.iterrows():
            radius = 5000 if row["jenis_faskes"] == "Rumah Sakit" else 3000
            folium.Circle(
                [row.geometry.y, row.geometry.x], radius=radius,
                color="#d97706" if radius == 5000 else "#16a34a",
                weight=1, fill=True, fill_opacity=0.035,
                tooltip=f"Simulasi radius {radius/1000:.0f} km · {safe_text(row['name'])}",
            ).add_to(m)

    folium.LayerControl(collapsed=True).add_to(m)
    m.fit_bounds([[miny, minx], [maxy, maxx]])
    return m

st.title("🏥 GeoHealth Surabaya")
st.caption("Geoportal persebaran fasilitas kesehatan, cakupan layanan, dan aksesibilitas Kota Surabaya")

try:
    boundary, facilities = load_data()
except Exception as exc:
    st.error(f"Gagal memuat data: {exc}")
    st.stop()

# Sidebar filters
st.sidebar.title("Pengaturan peta")
types = sorted(facilities["jenis_faskes"].dropna().unique().tolist())
selected_types = st.sidebar.multiselect("Jenis fasilitas", types, default=types)
show_boundary = st.sidebar.checkbox("Tampilkan batas Kota Surabaya", True)
show_radius = st.sidebar.checkbox("Tampilkan simulasi radius pelayanan", False)
st.sidebar.caption("Sumber titik fasilitas dan jaringan jalan: OpenStreetMap. Data dapat tidak lengkap atau berubah.")

shown = facilities[facilities["jenis_faskes"].isin(selected_types)].copy()
hospitals = facilities[facilities["jenis_faskes"] == "Rumah Sakit"]
puskesmas = facilities[facilities["jenis_faskes"] == "Puskesmas"]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Total fasilitas", f"{len(facilities):,}")
m2.metric("Rumah sakit", f"{len(hospitals):,}")
m3.metric("Puskesmas", f"{len(puskesmas):,}")
m4.metric("Fasilitas ditampilkan", f"{len(shown):,}")

tab_map, tab_search, tab_profile, tab_coverage = st.tabs(
    ["🗺️ Peta Surabaya", "📍 Cari terdekat & rute", "🏥 Profil fasilitas", "⭕ Analisis radius"]
)

if "origin" not in st.session_state:
    st.session_state.origin = None
if "nearest_ids" not in st.session_state:
    st.session_state.nearest_ids = []
if "route_result" not in st.session_state:
    st.session_state.route_result = None

with tab_map:
    st.subheader("Persebaran fasilitas kesehatan")
    st.caption("Peta otomatis dibatasi pada wilayah Kota Surabaya. Klik peta untuk menentukan titik asal.")
    map_obj = make_map(boundary, facilities, selected_types, show_radius, show_boundary)
    map_result = st_folium(
        map_obj, height=610, use_container_width=True, key="main_map",
        returned_objects=["last_clicked"],
    )
    if map_result and map_result.get("last_clicked"):
        click = map_result["last_clicked"]
        new_origin = {"lat": float(click["lat"]), "lon": float(click["lng"])}
        old = st.session_state.origin
        if old is None or abs(old["lat"]-new_origin["lat"]) > 1e-6 or abs(old["lon"]-new_origin["lon"]) > 1e-6:
            st.session_state.origin = new_origin
            st.session_state.nearest_ids = []
            st.session_state.route_result = None
            st.rerun()
    if st.session_state.origin:
        st.success(f"Titik asal dipilih: {st.session_state.origin['lat']:.5f}, {st.session_state.origin['lon']:.5f}")
        if st.button("Hapus titik asal"):
            st.session_state.origin = None
            st.session_state.nearest_ids = []
            st.session_state.route_result = None
            st.rerun()

with tab_search:
    st.subheader("Temukan fasilitas kesehatan terdekat")
    st.write("Pilih titik asal dengan mengeklik peta pada tab **Peta Surabaya**.")
    if st.session_state.origin:
        st.success(f"Lokasi asal: {st.session_state.origin['lat']:.5f}, {st.session_state.origin['lon']:.5f}")
        if st.button("Cari 5 fasilitas terdekat", type="primary"):
            if shown.empty:
                st.warning("Tidak ada fasilitas pada filter yang dipilih.")
            else:
                with st.spinner("Menghitung jarak..."):
                    projected = shown.to_crs(CRS_METER)
                    origin_pt = gpd.GeoSeries(
                        [Point(st.session_state.origin["lon"], st.session_state.origin["lat"])],
                        crs="EPSG:4326"
                    ).to_crs(CRS_METER).iloc[0]
                    distances = projected.geometry.distance(origin_pt) / 1000
                    result = projected.assign(distance_km=distances).sort_values("distance_km").head(5)
                    st.session_state.nearest_ids = result.index.tolist()
                    st.session_state.route_result = None
        ids = st.session_state.nearest_ids
        if ids:
            options = ids
            def option_label(idx):
                row = facilities.loc[idx]
                return f"{safe_text(row['name'])} · {row['jenis_faskes']} · {shown.loc[idx].geometry.distance(shown.loc[idx].geometry):.0f} km" if False else f"{safe_text(row['name'])} · {row['jenis_faskes']}"
            chosen = st.selectbox("Pilih tujuan", options, format_func=option_label, key="destination_select")
            row = facilities.loc[chosen]
            c1, c2 = st.columns(2)
            c1.write(f"**{safe_text(row['name'])}**")
            c2.write(f"**Jenis:** {safe_text(row['jenis_faskes'])}")
            st.write(f"Alamat: {safe_text(row.get('addr:full'), safe_text(row.get('addr:street')))}")
            if st.button("Hitung rute tercepat melalui jaringan jalan", type="primary"):
                try:
                    with st.spinner("Menghitung rute..."):
                        graph = load_graph()
                        origin = st.session_state.origin
                        start_node = ox.distance.nearest_nodes(graph, X=origin["lon"], Y=origin["lat"])
                        end_node = ox.distance.nearest_nodes(graph, X=row.geometry.x, Y=row.geometry.y)
                        path = nx.shortest_path(graph, start_node, end_node, weight="travel_time")
                        route_gdf = ox.routing.route_to_gdf(graph, path, weight="travel_time")
                        distance = float(route_gdf["length"].sum()/1000)
                        minutes = float(route_gdf["travel_time"].sum()/60)
                        st.session_state.route_result = {
                            "geojson": route_gdf.to_json(),
                            "distance": distance, "minutes": minutes,
                            "destination": int(chosen),
                        }
                except nx.NetworkXNoPath:
                    st.error("Tidak ditemukan jalur yang tersambung antara titik asal dan tujuan.")
                except Exception as exc:
                    st.error(f"Gagal menghitung rute: {exc}")
            route = st.session_state.route_result
            if route and route["destination"] == chosen:
                a, b = st.columns(2)
                a.metric("Jarak rute", f"{route['distance']:.2f} km")
                b.metric("Estimasi waktu", f"{route['minutes']:.1f} menit")
                route_gdf = gpd.GeoDataFrame.from_features(json.loads(route["geojson"])["features"], crs="EPSG:4326")
                route_map = folium.Map(location=[st.session_state.origin["lat"], st.session_state.origin["lon"]], zoom_start=13, tiles="OpenStreetMap")
                folium.GeoJson(route_gdf.to_json(), style_function=lambda f: {"color":"#1769ff","weight":6,"opacity":0.9}).add_to(route_map)
                folium.Marker([st.session_state.origin["lat"], st.session_state.origin["lon"]], tooltip="Titik asal", icon=folium.Icon(color="green")).add_to(route_map)
                folium.Marker([row.geometry.y, row.geometry.x], tooltip=safe_text(row["name"]), icon=folium.Icon(color="red", icon="plus-sign", prefix="glyphicon")).add_to(route_map)
                st_folium(route_map, height=430, use_container_width=True, key="route_map")
                roads = []
                for _, edge in route_gdf.iterrows():
                    road = edge.get("name")
                    if isinstance(road, list):
                        road = road[0] if road else None
                    if road and str(road) not in roads:
                        roads.append(str(road))
                st.markdown("**Nama jalan pada rute**")
                if roads:
                    for i, road in enumerate(roads, 1):
                        st.write(f"{i}. {road}")
                else:
                    st.info("Nama jalan tidak tersedia pada ruas rute ini.")
                origin = st.session_state.origin
                maps_url = f"https://www.google.com/maps/dir/{origin['lat']},{origin['lon']}/{row.geometry.y},{row.geometry.x}/"
                st.link_button("Buka navigasi di Google Maps", maps_url)
                st.caption("Estimasi waktu memakai kecepatan asumsi pada data jaringan jalan, bukan lalu lintas real-time.")

    else:
        st.info("Tentukan titik asal terlebih dahulu pada tab Peta Surabaya.")

with tab_profile:
    st.subheader("Direktori fasilitas kesehatan")
    profile_types = ["Rumah Sakit", "Puskesmas", "Klinik", "Praktik Dokter"]
    chosen_type = st.selectbox("Kategori fasilitas", profile_types)
    candidates = facilities[facilities["jenis_faskes"] == chosen_type].copy()
    search = st.text_input("Cari nama fasilitas")
    if search:
        candidates = candidates[candidates["name"].str.contains(search, case=False, na=False)]
    if candidates.empty:
        st.info("Tidak ada fasilitas yang cocok dengan pencarian.")
    else:
        candidates = candidates.sort_values("name")
        names = candidates["name"].tolist()
        selected_name = st.selectbox("Pilih fasilitas", names)
        item = candidates[candidates["name"] == selected_name].iloc[0]
        st.markdown(f"### {safe_text(item['name'])}")
        left, right = st.columns(2)
        left.write(f"**Kategori:** {safe_text(item['jenis_faskes'])}")
        left.write(f"**Alamat:** {safe_text(item.get('addr:full'), safe_text(item.get('addr:street')))}")
        left.write(f"**Operator:** {safe_text(item.get('operator'))}")
        right.write(f"**Telepon:** {safe_text(item.get('phone'))}")
        right.write(f"**Jam layanan:** {safe_text(item.get('opening_hours'))}")
        website = safe_text(item.get("website"), "")
        if website:
            st.link_button("Kunjungi situs fasilitas", website)
        lat, lon = item.geometry.y, item.geometry.x
        st.link_button("Lihat lokasi dan ulasan di Google Maps", f"https://www.google.com/maps/search/?api=1&query={lat}%2C{lon}")
        st.info("Rating dan teks ulasan pasien belum tersedia di dataset yang diunggah. Tautan di atas membuka Google Maps agar pengguna dapat memeriksa ulasan terbaru langsung dari sumbernya. Tidak ada rating atau ulasan yang dibuat-buat.")

with tab_coverage:
    st.subheader("Simulasi radius pelayanan")
    st.warning("Radius berikut merupakan asumsi analisis awal, bukan standar resmi dan bukan area jangkauan berdasarkan waktu tempuh jaringan jalan.")
    st.write("Asumsi: rumah sakit 5 km dan Puskesmas 3 km.")
    if st.button("Hitung cakupan simulasi"):
        with st.spinner("Menghitung area cakupan..."):
            facilities_m = facilities.to_crs(CRS_METER)
            boundary_m = boundary.to_crs(CRS_METER)
            primary = facilities_m[facilities_m["jenis_faskes"].isin(["Rumah Sakit", "Puskesmas"])].copy()
            if primary.empty:
                st.warning("Data rumah sakit dan Puskesmas tidak tersedia.")
            else:
                radii = primary["jenis_faskes"].map({"Rumah Sakit": 5000, "Puskesmas": 3000}).fillna(0)
                buffers = primary.geometry.buffer(radii)
                covered = buffers.union_all()
                city = boundary_m.geometry.union_all()
                covered_inside = city.intersection(covered)
                uncovered = city.difference(covered)
                total_area = city.area / 1e6
                covered_area = covered_inside.area / 1e6
                uncovered_area = uncovered.area / 1e6
                st.session_state.coverage = {
                    "total": total_area, "covered": covered_area, "uncovered": uncovered_area,
                    "percent": covered_area / total_area * 100 if total_area else 0,
                    "uncovered_geojson": gpd.GeoSeries([uncovered], crs=CRS_METER).to_crs("EPSG:4326").to_json(),
                }
    coverage = st.session_state.get("coverage")
    if coverage:
        a,b,c = st.columns(3)
        a.metric("Luas wilayah", f"{coverage['total']:.1f} km²")
        b.metric("Simulasi terlayani", f"{coverage['covered']:.1f} km²")
        c.metric("Simulasi belum terlayani", f"{coverage['uncovered']:.1f} km²")
        st.metric("Persentase cakupan simulasi", f"{coverage['percent']:.1f}%")
        st.caption("Area buffer yang saling tumpang tindih dihitung sebagai satu area, dan hanya bagian di dalam batas Surabaya yang dihitung.")
    else:
        st.info("Klik tombol untuk menjalankan analisis radius pelayanan.")

st.divider()
st.caption("GeoHealth Surabaya · Data fasilitas dan jaringan jalan bersumber dari OpenStreetMap. Kelengkapan dan ketepatan data perlu diverifikasi sebelum digunakan untuk keputusan darurat.")
