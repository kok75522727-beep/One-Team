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
    :root { color-scheme: dark; --primary-color:#ffd166; --background-color:#07101f; --secondary-background-color:#102447; --text-color:#edf5ff; }
    .stApp { background: radial-gradient(circle at 8% 0%, #162653 0%, #090e1b 38%, #050811 100%); color:#edf4ff; }
    [data-testid="stHeader"] { background: transparent; }
    .block-container { max-width: 1180px; padding: 1rem 1.2rem 2rem; }
    .brand { display:flex; align-items:center; gap:.7rem; margin-bottom:.8rem; }
    .brand-mark { width:42px; height:42px; border-radius:12px; display:grid; place-items:center; background:linear-gradient(135deg,#135cff,#7a35ff); font-size:1.35rem; }
    .brand h1 { margin:0; font-size:1.45rem; letter-spacing:.02em; }
    .brand p { margin:.1rem 0 0; color:#7f91b2; font-size:.78rem; }
    .panel { border:1px solid #294b7c; border-radius:16px; background:linear-gradient(145deg,rgba(20,32,59,.96),rgba(8,14,28,.98)); padding:1rem; box-shadow:0 14px 35px rgba(0,0,0,.32); }
    .section-title { color:#ffe690; font-weight:800; font-size:.92rem; margin:.1rem 0 .75rem; }
    .hint { color:#9fb5d6; font-size:.78rem; margin:.2rem 0 .7rem; }
    .status { border-radius:10px; padding:.6rem .75rem; background:#102445; border:1px solid #3466a4; color:#d9edff; font-size:.78rem; }
    .stTabs [data-baseweb="tab-list"] { gap:.5rem; background:#0a1326; padding:.45rem; margin-top:.7rem; border:1px solid #33598d; border-radius:12px; }
    .stTabs [data-baseweb="tab"] { flex:1 1 0; justify-content:center; height:2.55rem; min-width:0; padding:0 .5rem; border-radius:9px; background:#162846; color:#e6f2ff !important; font-size:.86rem; font-weight:800; white-space:nowrap; }
    .stTabs [aria-selected="true"] { background:linear-gradient(90deg,#ffc857,#ff9966); color:#151b2d !important; }
    .stButton > button { border-radius:10px; border:1px solid #87b8ff; background:linear-gradient(90deg,#9fd2ff,#7c9dff); color:#0b1324 !important; font-weight:800; text-shadow:none; box-shadow:0 5px 16px rgba(74,139,255,.28); }
    .stButton > button:hover { border-color:#fff2b5; background:linear-gradient(90deg,#ffe48e,#ffbd70); color:#201509 !important; }
    .stButton > button:disabled { background:#2a3855; border-color:#43516c; color:#a2aec3 !important; opacity:1; }
    div.st-key-simple_vip button { min-height:3rem; border:1px solid #ccefff; background:linear-gradient(135deg,#86d8ff,#6688ff); color:#09162b !important; font-size:.82rem; font-weight:900; box-shadow:0 7px 18px rgba(76,152,255,.34); }
    div.st-key-pro_vip button { min-height:3rem; border:1px solid #ffe39a; background:linear-gradient(135deg,#ffe081,#f49b52); color:#231506 !important; font-size:.82rem; font-weight:900; box-shadow:0 7px 18px rgba(244,163,65,.34); }
    div.st-key-simple_vip button:hover { background:linear-gradient(135deg,#b8ecff,#8aa5ff); color:#07101f !important; }
    div.st-key-pro_vip button:hover { background:linear-gradient(135deg,#fff0ae,#ffbf6b); color:#231506 !important; }
    div[data-testid="stFileUploader"] { border:1px dashed #628dca; border-radius:12px; padding:.35rem; background:#0d1d37; }
    [data-testid="stFileUploaderDropzone"] { background:#102447 !important; border:1px dashed #79aaf0 !important; }
    [data-testid="stFileUploaderDropzone"] * { color:#f1f8ff !important; font-weight:700 !important; }
    [data-testid="stFileUploaderDropzone"] span, [data-testid="stFileUploaderDropzone"] small, [data-testid="stFileUploaderDropzone"] p { font-size:.9rem !important; letter-spacing:.01em; }
    div[data-testid="stFileUploader"] button { min-height:2.55rem; color:#07101f !important; background:#aee8ff !important; border-color:#d5f7ff !important; font-size:.86rem; font-weight:900 !important; }
    .stApp [data-baseweb="select"] > div, .stApp [data-baseweb="input"] > div, .stApp input, .stApp textarea { background-color:#102447 !important; border-color:#6c9ce0 !important; color:#f4f8ff !important; }
    .stApp [data-baseweb="select"] *, .stApp [data-baseweb="input"] input, .stApp input::placeholder, .stApp textarea::placeholder { color:#f4f8ff !important; opacity:1; }
    .stApp [data-baseweb="select"] > div:hover, .stApp [data-baseweb="input"] > div:hover { border-color:#ffd166 !important; }
    div[data-testid="stSelectbox"] svg { fill:#ffd36d !important; }
    div[data-testid="stColorPicker"] input { background:#102447 !important; color:#f4f8ff !important; border-color:#6c9ce0 !important; }
    [data-testid="stWidgetLabel"] p, [data-testid="stCaptionContainer"], .stCaption { color:#b9cae4 !important; }
    button[role="switch"][aria-checked="true"] { background:#2ed39b !important; }
    button[role="switch"][aria-checked="false"] { background:#394966 !important; }
    div[data-baseweb="slider"] div[role="slider"] { background:#ffcb5c !important; border-color:#fff0af !important; }
    div[data-testid="stCheckbox"] label { color:#d9e8ff !important; }
    .voice-selected { border:1px solid #f2ba52; border-radius:10px; background:#2a2139; color:#fff0b5; padding:.45rem .65rem; font-size:.8rem; margin-top:.55rem; }
    @media (max-width: 720px) {
      .block-container { padding: .65rem .75rem 1.4rem; }
      .brand h1 { font-size: 1.1rem; }
      .brand p { font-size: .67rem; }
      .stTabs [data-baseweb="tab"] { padding:0 .32rem; font-size:.76rem; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def compact_choice(label: str, options: list[str], state_key: str, columns: int = 3) -> str:
    """Render keyboard-safe button choices for mobile selection-only fields."""
    st.caption(label)
    st.session_state.setdefault(state_key, options[0])
    for row_start in range(0, len(options), columns):
        row_options = options[row_start:row_start + columns]
        option_columns = st.columns(len(row_options))
        for column, option in zip(option_columns, row_options):
            selected = st.session_state[state_key] == option
            button_label = f"✓ {option}" if selected else option
            if column.button(button_label, key=f"{state_key}_{option}", use_container_width=True):
                st.session_state[state_key] = option
    return st.session_state[state_key]


st.session_state.setdefault("vip_plan", "Simple VIP")
brand_col, simple_vip_col, pro_vip_col = st.columns([3.2, 1.25, 1.05], gap="small")
with brand_col:
    st.markdown(
        '<div class="brand"><div class="brand-mark">▶</div><div><h1>Mg Khant</h1><p>Movie Recap Studio</p></div></div>',
        unsafe_allow_html=True,
    )
with simple_vip_col:
    if st.button("💎 Simple VIP", key="simple_vip", use_container_width=True):
        st.session_state.vip_plan = "Simple VIP"
with pro_vip_col:
    if st.button("👑 Pro VIP", key="pro_vip", use_container_width=True):
        st.session_state.vip_plan = "Pro VIP"
st.caption(f"Plan: {st.session_state.vip_plan}")

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
    st.caption("Turn on a feature to reveal its settings here.")
    if blur_on:
        st.markdown('<div class="section-title">Blur Settings</div>', unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        c1.slider("X", 0, 100, 50, key="blur_x")
        c2.slider("Y", 0, 100, 78, key="blur_y")
        c1, c2 = st.columns(2)
        c1.slider("Width", 1, 100, 50, key="blur_width")
        c2.slider("Height", 1, 100, 12, key="blur_height")
        st.slider("Strength", 1, 30, 12, key="blur_strength")
    if subtitle_on:
        st.markdown('<div class="section-title">Subtitle Settings</div>', unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            compact_choice("Font", ["Pyidaungsu", "Noto Myanmar", "Padauk"], "subtitle_font", columns=1)
        c2.slider("Size", 12, 72, 38, key="subtitle_size")
        c1, c2 = st.columns(2)
        c1.color_picker("Subtitle Color", "#FFD166", key="subtitle_color")
        with c2:
            compact_choice("Outline", ["None", "Thin", "Medium", "Thick"], "outline", columns=2)
        c1, c2 = st.columns(2)
        c1.slider("X", 0, 100, 50, key="subtitle_x")
        c2.slider("Y", 0, 100, 78, key="subtitle_y")
        st.checkbox("Solid Background", key="solid_background")
        st.slider("Background Opacity", 0, 100, 65, key="subtitle_opacity")
    if st.button("Apply", use_container_width=True, key="apply_settings"):
        st.session_state.applied = True
    if st.session_state.get("applied"):
        st.success("UI settings applied locally.")
    st.markdown('</div>', unsafe_allow_html=True)

st.write("")
tab_format, tab_edit, tab_logo, tab_voice = st.tabs(["Format", "Copyright Edit", "Logo", "Voice"])

with tab_format:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    with c1:
        compact_choice("Aspect", ["9:16", "16:9", "1:1"], "aspect", columns=1)
    with c2:
        compact_choice("Type", ["Movie Recap", "Simple Movie"], "movie_type", columns=1)
    with c3:
        compact_choice("Quality", ["720p", "1080p"], "quality", columns=1)
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
        compact_choice("Original Audio", ["Mute All", "Keep Action Sound", "Keep All"], "original_audio", columns=1)
    st.file_uploader("Background Music", type=["mp3", "wav", "m4a"], key="music")
    st.markdown('</div>', unsafe_allow_html=True)

with tab_logo:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.caption("Image logo and text logo stay separate.")
    st.file_uploader("Image Logo", type=["png", "jpg", "jpeg"], key="logo")
    logo_position_col, text_logo_col, text_motion_col = st.columns([1, 1.45, 1.1])
    with logo_position_col:
        compact_choice("Image Position", ["Left", "Right"], "image_logo_position", columns=1)
    with text_logo_col:
        st.text_input("Text Logo", placeholder="Enter logo text", key="text_logo")
    with text_motion_col:
        compact_choice("Text Motion", ["Left", "Right", "Scroll"], "text_logo_motion", columns=1)
    st.markdown('</div>', unsafe_allow_html=True)

with tab_voice:
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.caption("Choose a Burmese voice")
    voices = [
        ("🧑‍🎤", "ကိုခန့်"), ("👩‍🎤", "မသီရိ"), ("🧑‍🚀", "ကိုမင်း"), ("👩‍🚀", "မဝင်း"), ("🧑‍🏫", "ကိုဇင်"),
        ("👩‍🏫", "မသွယ်"), ("🧑‍💼", "ကိုလင်း"), ("👩‍💼", "မေသူ"), ("🧑‍🎨", "ကိုထက်"), ("👩‍🎨", "မနန်း"),
    ]
    st.session_state.setdefault("voice", voices[0][1])
    for row_start in range(0, len(voices), 5):
        voice_columns = st.columns(5)
        for column, (avatar, name) in zip(voice_columns, voices[row_start:row_start + 5]):
            with column:
                label = f"{avatar} {name}"
                if st.button(label, key=f"voice_{name}", use_container_width=True):
                    st.session_state.voice = name
    st.markdown(f'<div class="voice-selected">ရွေးထားသောအသံ: {st.session_state.voice}</div>', unsafe_allow_html=True)
    st.slider("Speed", min_value=0.75, max_value=1.5, value=1.0, step=0.05, key="speed")
    st.caption("ကာတွန်းပုံအသေးနဲ့ မြန်မာနာမည် ၁၀ မျိုးကို UI preview အဖြစ် ပြထားပါတယ်။")
    st.markdown('</div>', unsafe_allow_html=True)

st.caption("UI-only preview: no API, database, video processing, or export is connected yet.")

st.divider()
st.button("Export Video", use_container_width=True, disabled=True)
