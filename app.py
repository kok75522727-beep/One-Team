from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="Mg Khant",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    :root { color-scheme: dark; }
    .stApp { background: #070a12; }
    [data-testid="stHeader"] { background: transparent; }
    .block-container { max-width: 1180px; padding: 1rem 1.2rem 2rem; }
    .brand { display:flex; align-items:center; gap:.7rem; margin-bottom:.8rem; }
    .brand-mark { width:42px; height:42px; border-radius:12px; display:grid; place-items:center; background:linear-gradient(135deg,#135cff,#7a35ff); font-size:1.35rem; }
    .brand h1 { margin:0; font-size:1.45rem; letter-spacing:.02em; }
    .brand p { margin:.1rem 0 0; color:#7f91b2; font-size:.78rem; }
    .panel { border:1px solid #1b2a43; border-radius:16px; background:linear-gradient(145deg,#0d1422,#0a0f1a); padding:1rem; box-shadow:0 14px 35px rgba(0,0,0,.22); }
    .section-title { color:#dbe7ff; font-weight:700; font-size:.92rem; margin:.1rem 0 .75rem; }
    .hint { color:#8595b3; font-size:.78rem; margin:.2rem 0 .7rem; }
    .status { border-radius:10px; padding:.6rem .75rem; background:#0b1729; border:1px solid #1d355a; color:#9eb3d4; font-size:.78rem; }
    .stTabs [data-baseweb="tab-list"] { gap:.35rem; background:#0a101c; padding:.35rem; border:1px solid #1b2a43; border-radius:12px; }
    .stTabs [data-baseweb="tab"] { height:2.2rem; padding:0 .75rem; border-radius:8px; color:#92a2be; font-size:.78rem; }
    .stTabs [aria-selected="true"] { background:linear-gradient(90deg,#1262ff,#7544ff); color:white; }
    .stButton > button { border-radius:10px; border:1px solid #285cff; background:linear-gradient(90deg,#145cff,#713dff); color:white; font-weight:700; }
    div[data-testid="stFileUploader"] { border:1px dashed #2c4164; border-radius:12px; padding:.35rem; background:#0b1220; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="brand"><div class="brand-mark">▶</div><div><h1>Mg Khant</h1><p>Movie Recap Studio</p></div></div>',
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("Menu")
    st.radio("Page", ["Editor", "VIP", "Settings"], label_visibility="collapsed")
    st.divider()
    st.caption("UI preview mode")

left, right = st.columns([1.28, 1], gap="medium")

with left:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Video Source</div>', unsafe_allow_html=True)
    video_file = st.file_uploader(
        "Upload Video",
        type=["mp4", "mov", "mkv", "webm"],
        label_visibility="collapsed",
        key="video_file",
    )
    if video_file:
        st.video(video_file)
        size_mb = video_file.size / (1024 * 1024)
        st.markdown(f'<div class="status">{video_file.name} · {size_mb:.1f} MB</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="status">Choose a local video file to see the preview here.</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

with right:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Quick Controls</div>', unsafe_allow_html=True)
    blur_on = st.toggle("Blur", value=False, key="blur_on")
    subtitle_on = st.toggle("Subtitle", value=True, key="subtitle_on")
    st.caption("Turn on a feature to reveal its settings below.")
    st.markdown('</div>', unsafe_allow_html=True)

st.write("")
tab_format, tab_edit, tab_logo, tab_voice = st.tabs(["Format", "Copyright Edit", "Logo", "Voice"])

with tab_format:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    with c1:
        st.selectbox("Aspect", ["9:16", "16:9", "1:1"], key="aspect")
    with c2:
        st.selectbox("Type", ["Movie Recap", "Simple Movie"], key="movie_type")
    with c3:
        st.selectbox("Quality", ["720p", "1080p"], key="quality")
    st.markdown('</div>', unsafe_allow_html=True)

with tab_edit:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.caption("Only enable the edits you need.")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.toggle("Auto Zoom", key="auto_zoom")
        st.toggle("Mirror", key="mirror")
    with c2:
        st.toggle("Color Filter", key="color_filter")
        st.toggle("Pitch Alter", key="pitch_alter")
    with c3:
        st.toggle("Background Blur", key="background_blur")
        st.selectbox("Original Audio", ["Mute All", "Keep Action Sound", "Keep All"], key="original_audio")
    st.file_uploader("Background Music", type=["mp3", "wav", "m4a"], key="music")
    st.markdown('</div>', unsafe_allow_html=True)

with tab_logo:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.caption("Image logo and text logo stay separate.")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.file_uploader("Image Logo 1", type=["png", "jpg", "jpeg"], key="logo1")
    with c2:
        st.file_uploader("Image Logo 2", type=["png", "jpg", "jpeg"], key="logo2")
    with c3:
        st.file_uploader("Image Logo 3", type=["png", "jpg", "jpeg"], key="logo3")
    st.selectbox("Image Logo Position", ["Left", "Right"], key="image_logo_position")
    st.text_input("Text Logo", placeholder="Enter logo text", key="text_logo")
    st.selectbox("Text Logo Motion", ["Left", "Right", "Scroll"], key="text_logo_motion")
    st.markdown('</div>', unsafe_allow_html=True)

with tab_voice:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    voices = ["Voice 01", "Voice 02", "Voice 03", "Voice 04", "Voice 05", "Voice 06", "Voice 07", "Voice 08", "Voice 09", "Voice 10"]
    st.selectbox("Voice", voices, key="voice")
    st.slider("Speed", min_value=0.75, max_value=1.5, value=1.0, step=0.05, key="speed")
    st.caption("10 voice choices are shown here for the UI preview.")
    st.markdown('</div>', unsafe_allow_html=True)

if blur_on:
    with st.expander("Blur Settings", expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        c1.slider("X", 0, 100, 50, key="blur_x")
        c2.slider("Y", 0, 100, 78, key="blur_y")
        c3.slider("Width", 1, 100, 50, key="blur_width")
        c4.slider("Height", 1, 100, 12, key="blur_height")
        st.slider("Strength", 1, 30, 12, key="blur_strength")

if subtitle_on:
    with st.expander("Subtitle Settings", expanded=True):
        c1, c2, c3 = st.columns(3)
        c1.selectbox("Font", ["Pyidaungsu", "Noto Sans Myanmar", "Padauk"], key="subtitle_font")
        c2.slider("Size", 12, 72, 38, key="subtitle_size")
        c3.color_picker("Subtitle Color", "#FFFFFF", key="subtitle_color")
        c1, c2, c3 = st.columns(3)
        c1.slider("X", 0, 100, 50, key="subtitle_x")
        c2.slider("Y", 0, 100, 78, key="subtitle_y")
        c3.selectbox("Outline", ["None", "Thin", "Medium", "Thick"], key="outline")
        st.checkbox("Solid Background", key="solid_background")
        st.slider("Background Opacity", 0, 100, 65, key="subtitle_opacity")

st.write("")
left_button, right_button = st.columns([1, 3])
with left_button:
    if st.button("Apply", use_container_width=True):
        st.session_state.applied = True
with right_button:
    if st.session_state.get("applied"):
        st.success("UI settings applied locally. Processing is not connected in this UI-only build.")
    else:
        st.caption("UI-only preview: no API, database, video processing, or export is connected yet.")

st.divider()
st.button("Export Video", use_container_width=True, disabled=True)
