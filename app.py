"""
그림판 스타일 이미지 측정 웹앱
- 이미지 업로드 후 이미지 내/외부 모두에서 선을 그어 길이 측정
- 픽셀 / cm 단위 전환
- 그림판 UI 스타일
"""

import io
import base64
import json
import warnings
import logging

# st.components.v1.html 사용 중단 경고 억제 (2026-06 이전까지 호환 유지)
warnings.filterwarnings("ignore", message=".*st\\.components\\.v1\\.html.*")
warnings.filterwarnings("ignore", message=".*Please replace.*st\\.iframe.*")

# Streamlit이 logging으로 출력하는 deprecation 메시지 억제
logging.getLogger("streamlit").setLevel(logging.ERROR)

import streamlit as st
from PIL import Image, ExifTags
try:
    import exifread as _exifread
    _HAS_EXIFREAD = True
except ImportError:
    _HAS_EXIFREAD = False

# ──────────────────────────────────────────────────────────────────────────────
# 페이지 설정
# ──────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    layout="wide",
    page_title="그림판 측정기",
    page_icon="📐",
    initial_sidebar_state="collapsed",
)

# ──────────────────────────────────────────────────────────────────────────────
# 전체 CSS — 그림판 느낌
# ──────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Streamlit 상단 헤더/툴바 완전히 숨김 */
[data-testid="stHeader"] { display: none !important; }
[data-testid="collapsedControl"] { display: none !important; }
header[data-testid="stHeader"] { display: none !important; }

html, body, [data-testid="stAppViewContainer"] {
    background: #ece9d8;
    font-family: 'Segoe UI', Tahoma, sans-serif;
}
.block-container {
    padding: 0 !important;
    max-width: 100% !important;
}
/* 파일 업로드 바 */
.upload-bar {
    background: linear-gradient(to bottom, #f0f0f0, #e0e0e0);
    border-bottom: 1px solid #b0b0b0;
    padding: 4px 10px;
    display: flex;
    align-items: center;
    gap: 8px;
}
/* 모바일: 업로드/정보 영역 */
@media (max-width: 768px) {
    .block-container { padding: 4px !important; }
    /* Streamlit iframe이 스크롤되도록 허용 */
    iframe { border: none !important; }
}
</style>
<script>
// 뷰포트 메타태그 (Streamlit이 기본으로 추가하지 않는 경우 대비)
(function() {
  var m = document.querySelector('meta[name="viewport"]');
  if (!m) {
    m = document.createElement('meta');
    m.name = 'viewport';
    document.head.prepend(m);
  }
  m.content = 'width=device-width, initial-scale=1.0, maximum-scale=5.0';
})();
</script>
""", unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────────────
# Session state 초기화
# ──────────────────────────────────────────────────────────────────────────────
DEFAULTS = {
    "img_data": None,
    "img_width_px": 0,
    "img_height_px": 0,
    "img_mime": "image/png",
    "unit": "cm",
    "dpi": 96,
    "line_color": "#FF0000",
    "line_width": 2,
    "exif_info": None,
}
for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


def read_exif(img_obj: Image.Image, raw_bytes: bytes = None) -> dict:
    """PIL + exifread 두 단계로 EXIF를 읽어 dict로 반환.
    PIL이 실패하면 exifread로 재시도.  raw_bytes는 업로드 원본 바이트.
    """
    result = {}

    def ifd_ratio(val):
        try:
            if hasattr(val, 'numerator'):
                return float(val.numerator) / float(val.denominator) if val.denominator else 0.0
            if isinstance(val, tuple) and len(val) == 2:
                return val[0] / val[1] if val[1] else 0.0
            return float(val)
        except Exception:
            return 0.0

    # ── 1단계: PIL _getexif() ──────────────────────────────────────────
    raw_exif = None
    try:
        raw_exif = img_obj._getexif()
    except Exception:
        pass

    if raw_exif:
        id_names = ExifTags.TAGS
        for tag_id, value in raw_exif.items():
            name = id_names.get(tag_id, str(tag_id))
            result[name] = value
        result['_source'] = 'PIL'

    # ── 2단계: exifread fallback ───────────────────────────────────────
    # PIL이 읽지 못했거나 핵심 태그가 없을 때 exifread로 보완
    need_fallback = (
        _HAS_EXIFREAD and raw_bytes is not None and
        (not raw_exif or ('FocalLength' not in result and 'FocalLengthIn35mmFilm' not in result))
    )
    if need_fallback:
        try:
            import io as _io
            er_tags = _exifread.process_file(_io.BytesIO(raw_bytes), details=False, strict=False)

            def er_ratio(tag):
                """exifread IfdTag → float"""
                try:
                    v = tag.values
                    if isinstance(v, list) and len(v) > 0:
                        r = v[0]
                        if hasattr(r, 'num'):  # Ratio object
                            return float(r.num) / float(r.den) if r.den else 0.0
                        return float(r)
                    return float(v)
                except Exception:
                    return 0.0

            def er_str(key):
                t = er_tags.get(key)
                return str(t.values).strip() if t else ''

            # 카메라
            if not result.get('Make'):
                result['Make']  = er_str('Image Make')
            if not result.get('Model'):
                result['Model'] = er_str('Image Model')

            # 렌즈
            if not result.get('LensModel'):
                result['LensModel'] = er_str('EXIF LensModel') or er_str('MakerNote LensType')

            # 촬영 정보
            if not result.get('FNumber'):
                t = er_tags.get('EXIF FNumber')
                if t: result['FNumber'] = (t.values[0].num, t.values[0].den) if t.values else None
            if not result.get('ExposureTime'):
                t = er_tags.get('EXIF ExposureTime')
                if t: result['ExposureTime'] = (t.values[0].num, t.values[0].den) if t.values else None
            if not result.get('ISOSpeedRatings'):
                t = er_tags.get('EXIF ISOSpeedRatings')
                if t:
                    try: result['ISOSpeedRatings'] = int(str(t.values[0]))
                    except: pass

            # 초점거리
            if not result.get('FocalLength'):
                t = er_tags.get('EXIF FocalLength')
                if t: result['FocalLength'] = (t.values[0].num, t.values[0].den) if t.values else None
            if not result.get('FocalLengthIn35mmFilm'):
                t = er_tags.get('EXIF FocalLengthIn35mmFilm')
                if t:
                    try: result['FocalLengthIn35mmFilm'] = int(str(t.values[0]))
                    except: pass

            # 센서 해상도
            if not result.get('FocalPlaneXResolution'):
                t = er_tags.get('EXIF FocalPlaneXResolution')
                if t: result['FocalPlaneXResolution'] = (t.values[0].num, t.values[0].den) if t.values else None
            if not result.get('FocalPlaneResolutionUnit'):
                t = er_tags.get('EXIF FocalPlaneResolutionUnit')
                if t:
                    try: result['FocalPlaneResolutionUnit'] = int(str(t.values[0]))
                    except: pass

            result['_source'] = 'exifread' if not raw_exif else 'PIL+exifread'
        except Exception:
            pass

    if not result:
        return result

    # ── 공통 파생 계산 ─────────────────────────────────────────────────
    fl   = result.get('FocalLength')
    fl35 = result.get('FocalLengthIn35mmFilm')
    result['_focal_mm']   = ifd_ratio(fl)  if fl   is not None else None
    result['_focal_35mm'] = int(fl35)       if fl35 is not None else None

    fpx_res  = result.get('FocalPlaneXResolution')
    fpx_unit = result.get('FocalPlaneResolutionUnit', 2)
    img_w    = img_obj.width
    if fpx_res and result['_focal_mm']:
        res_val = ifd_ratio(fpx_res)
        if res_val > 0:
            unit_mm = 25.4 if int(fpx_unit) == 2 else 10.0
            result['_focal_px_sensor'] = result['_focal_mm'] * res_val / unit_mm
        else:
            result['_focal_px_sensor'] = None
    else:
        result['_focal_px_sensor'] = None

    if result['_focal_35mm'] and img_w:
        result['_focal_px_35mm'] = result['_focal_35mm'] * img_w / 36.0
    else:
        result['_focal_px_35mm'] = None

    result['_make']  = str(result.get('Make',  '') or '').strip()
    result['_model'] = str(result.get('Model', '') or '').strip()
    result['_lens']  = str(result.get('LensModel', result.get('LensSpecification', '')) or '').strip()

    iso = result.get('ISOSpeedRatings', result.get('PhotographicSensitivity', ''))
    result['_iso'] = str(iso) if iso else ''

    exp = result.get('ExposureTime')
    ev  = ifd_ratio(exp) if exp else 0
    result['_exp'] = (f"1/{int(round(1/ev))}s" if ev and ev < 1 else
                      (f"{ev:.1f}s" if ev else ''))

    fno = result.get('FNumber')
    result['_fno'] = f"f/{ifd_ratio(fno):.1f}" if fno else ''

    return result

# ──────────────────────────────────────────────────────────────────────────────
# 파일 업로드 바 (메인 영역 상단)
# ──────────────────────────────────────────────────────────────────────────────
col_up, col_info = st.columns([4, 6])
with col_up:
    uploaded = st.file_uploader(
        "🖼️ 이미지 파일 선택 (PNG·JPG·BMP·GIF·TIFF·WEBP)",
        type=["png", "jpg", "jpeg", "bmp", "gif", "tiff", "webp"],
    )
    if uploaded:
        raw = uploaded.read()
        img_obj = Image.open(io.BytesIO(raw))
        st.session_state["exif_info"] = read_exif(img_obj, raw_bytes=raw)
        if img_obj.mode not in ("RGB", "RGBA"):
            img_obj = img_obj.convert("RGB")
        buf = io.BytesIO()
        img_obj.save(buf, format="PNG")
        st.session_state["img_data"] = buf.getvalue()
        st.session_state["img_width_px"] = img_obj.width
        st.session_state["img_height_px"] = img_obj.height
        st.session_state["img_mime"] = "image/png"

with col_info:
    if st.session_state["img_data"]:
        w_px = st.session_state["img_width_px"]
        h_px = st.session_state["img_height_px"]
        ex   = st.session_state.get("exif_info") or {}
        dpi  = st.session_state.get("dpi", 96)
        ppcm = dpi / 2.54  # pixels per cm
        w_cm = w_px / ppcm
        h_cm = h_px / ppcm

        # ── 기본 크기 줄 ──
        size_html = (
            f"<div style='padding:4px 0 2px;font-size:13px;color:#333;'>"
            f"📋 이미지 크기: <b>{w_px} px</b> × <b>{h_px} px</b>"
            f" &nbsp;<span style='color:#777;font-size:12px;'>("
            f"<b>{w_cm:.1f} cm</b> × <b>{h_cm:.1f} cm</b> @ {dpi} dpi)</span>"
            f"</div>"
        )

        # ── EXIF 줄 ──
        exif_rows = []

        # 카메라/렌즈
        cam = ' '.join(filter(None, [ex.get('_make',''), ex.get('_model','')])).strip()
        lens = str(ex.get('_lens', '')).strip()
        if cam:
            exif_rows.append(f"<b>카메라:</b> {cam}")
        if lens and lens not in ('', '[]'):
            exif_rows.append(f"<b>렌즈:</b> {lens}")

        # 촬영 정보
        shot_parts = []
        if ex.get('_fno'):  shot_parts.append(ex['_fno'])
        if ex.get('_exp'):  shot_parts.append(ex['_exp'])
        if ex.get('_iso'):  shot_parts.append(f"ISO {ex['_iso']}")
        if shot_parts:
            exif_rows.append(f"<b>촬영:</b> {' &nbsp;·&nbsp; '.join(shot_parts)}")

        # 초점거리
        fl_mm   = ex.get('_focal_mm')
        fl_35   = ex.get('_focal_35mm')
        fl_px_s = ex.get('_focal_px_sensor')
        fl_px_e = ex.get('_focal_px_35mm')

        fl_parts = []
        if fl_mm:  fl_parts.append(f"실제 <b>{fl_mm:.1f} mm</b>")
        if fl_35:  fl_parts.append(f"35mm환산 <b>{fl_35} mm</b>")
        if fl_parts:
            exif_rows.append(f"<b>초점거리:</b> {' &nbsp;/&nbsp; '.join(fl_parts)}")

        # 픽셀 환산 초점거리
        if fl_px_s:
            fl_px_s_cm = fl_px_s / ppcm
            exif_rows.append(
                f"<b>픽셀 초점거리 <i>f</i><sub>px</sub></b> (센서 해상도 기준): "
                f"<b style='color:#0055cc'>{fl_px_s:.1f} px</b>"
                f" &nbsp;<span style='color:#777;font-size:11px;'>= <b>{fl_px_s_cm:.2f} cm</b> @ {dpi} dpi</span>"
            )
        if fl_px_e:
            fl_px_e_cm = fl_px_e / ppcm
            exif_rows.append(
                f"<b>픽셀 초점거리 <i>f</i><sub>px</sub></b> (35mm환산 기준): "
                f"<b style='color:#0055cc'>{fl_px_e:.1f} px</b>"
                f" &nbsp;<span style='color:#777;font-size:11px;'>= <b>{fl_px_e_cm:.2f} cm</b> @ {dpi} dpi"
                f" &nbsp;·&nbsp; {fl_35}mm × {w_px}px / 36mm</span>"
            )

        if not exif_rows:
            exif_rows.append("<span style='color:#aaa;font-size:11px;'>EXIF 정보 없음 (PNG·BMP 등은 미지원)</span>")
        else:
            src = ex.get('_source', '')
            src_label = {'PIL': 'PIL', 'exifread': 'exifread (확장)', 'PIL+exifread': 'PIL + exifread (확장)'}.get(src, src)
            if src_label:
                exif_rows.append(f"<span style='color:#bbb;font-size:10px;'>읽기 방법: {src_label}</span>")

        exif_html = "<div style='font-size:12px;color:#333;line-height:1.9;'>" + \
                    "<br>".join(exif_rows) + "</div>"

        st.markdown(size_html + exif_html, unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────────────────
# 메인 영역
# ──────────────────────────────────────────────────────────────────────────────

if st.session_state["img_data"] is None:
    # 안내 화면
    st.markdown("""
    <div style="
        background:#808080; min-height:600px;
        display:flex; justify-content:center; align-items:center;
    ">
      <div style="
          background:white; width:620px; height:420px;
          box-shadow:4px 4px 12px rgba(0,0,0,0.55);
          display:flex; flex-direction:column;
          justify-content:center; align-items:center; gap:14px;
      ">
        <div style="font-size:56px;">🖼️</div>
        <div style="font-weight:bold;font-size:22px;color:#222;">이미지를 업로드하세요</div>
        <div style="font-size:14px;color:#777;">위쪽 파일 선택 버튼에서 이미지를 선택하세요</div>
        <div style="font-size:12px;color:#aaa;">PNG · JPG · BMP · GIF · TIFF · WEBP 지원</div>
        <hr style="width:80%;border-color:#ddd;">
        <div style="font-size:13px;color:#555;text-align:center;line-height:1.8;">
          ① 이미지 업로드 &nbsp;→&nbsp; ② 선 측정 도구로 두 점 클릭<br>
          ③ 이미지 <b>내부 및 외부</b> 모두 측정 가능<br>
          ④ 픽셀 / cm 단위 전환 지원
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)
else:
    # 이미지 base64
    img_b64 = base64.b64encode(st.session_state["img_data"]).decode()
    mime = st.session_state["img_mime"]
    img_w = st.session_state["img_width_px"]
    img_h = st.session_state["img_height_px"]
    unit = st.session_state["unit"]
    dpi = st.session_state["dpi"]
    line_color = st.session_state["line_color"]
    line_width = st.session_state["line_width"]

    unit_sel_px = "selected" if unit == "px" else ""
    unit_sel_cm = "selected" if unit == "cm" else ""
    dpi_display = "inline-block" if unit == "cm" else "none"
    dpi_label_display = "inline" if unit == "cm" else "none"

    canvas_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{
  background:#ece9d8;
  font-family:'Segoe UI',Tahoma,sans-serif;
  overflow:hidden;
  user-select:none;
}}
#app{{display:flex;flex-direction:column;height:100vh;}}

/* 제목바/메뉴바 제거됨 */

/* ── 리본 툴바 ── */
#ribbon{{
  background:linear-gradient(to bottom,#f8f8f8,#e8e8e8);
  border-bottom:2px solid #b0b0b0;
  padding:5px 8px;
  display:flex;
  align-items:center;
  gap:6px;
  flex-wrap:wrap;
  box-shadow:0 2px 4px rgba(0,0,0,0.12);
  flex-shrink:0;
  min-height:46px;
}}
.tb-sep{{width:1px;height:32px;background:#b0b0b0;margin:0 4px;}}
.tb-label{{font-size:11px;color:#444;margin-right:2px;}}
.tb-group{{
  display:flex;flex-direction:column;align-items:center;gap:2px;
  padding:2px 6px;border-right:1px solid #c8c8c8;
}}
.tb-group:last-child{{border-right:none;}}
.tb-group-label{{font-size:9px;color:#777;margin-top:2px;}}
.tb-row{{display:flex;align-items:center;gap:3px;}}

.tb-btn{{
  background:linear-gradient(to bottom,#fafafa,#e0e0e0);
  border:1px solid #a8a8a8;
  border-radius:3px;
  padding:3px 9px;
  font-size:11px;
  cursor:pointer;
  min-height:28px;
  color:#111;
  box-shadow:1px 1px 2px rgba(0,0,0,0.15);
  white-space:nowrap;
}}
.tb-btn:hover{{background:linear-gradient(to bottom,#deeeff,#b8d4ff);border-color:#4477cc;}}
.tb-btn.active{{
  background:linear-gradient(to bottom,#c0d8f8,#98bcf0);
  border-color:#2255aa;
  box-shadow:inset 1px 1px 3px rgba(0,0,0,0.2);
}}
select.tb-select{{
  background:linear-gradient(to bottom,#fafafa,#e8e8e8);
  border:1px solid #a8a8a8;border-radius:3px;
  padding:2px 4px;font-size:11px;height:26px;color:#111;
}}
input[type=color].tb-color{{
  width:34px;height:28px;border:1px solid #a8a8a8;border-radius:3px;
  padding:1px;cursor:pointer;background:white;
}}
input[type=number].tb-num{{
  width:52px;height:26px;border:1px solid #a8a8a8;border-radius:3px;
  padding:2px 4px;font-size:11px;text-align:center;
}}
input[type=range].tb-range{{
  width:72px;accent-color:#3366cc;
}}

/* ── 캔버스 + 패널 ── */
#work-area{{
  display:flex;
  flex:1;
  overflow:hidden;
}}
#scroll-area{{
  flex:1;
  overflow:auto;
  background:#808080;
  padding:24px;
  cursor:crosshair;
  position:relative;
}}
#canvas-container{{
  position:relative;
  display:inline-block;
  flex-shrink:0;
  box-shadow:5px 5px 12px rgba(0,0,0,0.55);
}}
#bg-canvas,#draw-canvas{{
  position:absolute;top:0;left:0;display:block;
}}
#bg-canvas{{z-index:1;}}
#draw-canvas{{z-index:2;cursor:crosshair;}}

/* ── 결과 패널 ── */
#result-panel{{
  width:290px;
  min-width:290px;
  background:#f4f4f4;
  border-left:2px solid #b0b0b0;
  display:flex;
  flex-direction:column;
  overflow:hidden;
}}
#result-header{{
  background:linear-gradient(to bottom,#f0f0f0,#dcdcdc);
  border-bottom:1px solid #c0c0c0;
  padding:7px 10px;
  font-size:13px;
  font-weight:bold;
  color:#222;
}}
#result-body{{
  flex:1;
  overflow-y:auto;
  padding:6px;
}}
.r-row{{
  display:flex;align-items:center;gap:5px;
  background:white;border:1px solid #d4d4d4;border-radius:4px;
  padding:5px 7px;margin-bottom:4px;
  box-shadow:1px 1px 3px rgba(0,0,0,0.08);
}}
.r-row:hover{{background:#edf4ff;border-color:#99bbee;}}
.r-num{{
  background:#3366cc;color:white;border-radius:50%;
  width:20px;height:20px;display:flex;align-items:center;justify-content:center;
  font-size:10px;font-weight:bold;flex-shrink:0;
}}
.r-info{{flex:1;font-size:11px;}}
.r-len{{font-size:13px;font-weight:bold;color:#111;}}
.r-coord{{font-size:10px;color:#888;}}
.r-del{{
  background:none;border:none;color:#cc2222;cursor:pointer;
  font-size:15px;padding:0 2px;line-height:1;flex-shrink:0;
}}
.r-del:hover{{color:#ff0000;}}
#result-empty{{
  color:#aaa;font-size:12px;text-align:center;padding:30px 12px;line-height:1.8;
}}
#result-summary{{
  background:linear-gradient(to bottom,#e4ecf8,#d4e0f4);
  border-top:1px solid #c0c8d8;
  padding:8px 10px;
  font-size:12px;color:#333;
}}

/* ── 상태바 ── */
#statusbar{{
  background:linear-gradient(to bottom,#e8e8e8,#d4d4d4);
  border-top:1px solid #b4b4b4;
  padding:3px 10px;
  font-size:11px;color:#333;
  display:flex;gap:14px;align-items:center;
  flex-shrink:0;
  overflow-x:auto;
  white-space:nowrap;
}}
.sb-item{{padding-right:12px;border-right:1px solid #c0c0c0;}}
.sb-item:last-child{{border-right:none;}}

/* ── 모바일 패널 오버레이 ── */
#panel-overlay{{
  display:none;
  position:fixed;inset:0;background:rgba(0,0,0,0.35);z-index:49;
}}

/* ── 반응형: 태블릿 이하 (≤900px) ── */
@media (max-width:900px){{
  #ribbon{{
    flex-wrap:nowrap;
    overflow-x:auto;
    -webkit-overflow-scrolling:touch;
    padding:4px 6px;
    gap:4px;
  }}
  .tb-group{{padding:2px 4px;}}
  .tb-btn{{padding:5px 8px;font-size:12px;min-height:34px;}}
  select.tb-select{{height:30px;font-size:12px;}}
  input[type=number].tb-num{{height:30px;}}
  #result-panel{{
    position:fixed;
    right:0;top:0;bottom:0;
    width:280px;
    z-index:50;
    transform:translateX(100%);
    transition:transform 0.25s ease;
    box-shadow:-4px 0 16px rgba(0,0,0,0.25);
  }}
  #result-panel.open{{transform:translateX(0);}}
  #panel-overlay.open{{display:block;}}
  #panel-toggle{{display:flex !important;}}
}}

/* ── 반응형: 스마트폰 (≤480px) ── */
@media (max-width:480px){{
  .tb-group-label{{display:none;}}
  .tb-label{{display:none;}}
  .tb-btn{{padding:6px 10px;font-size:13px;min-height:38px;}}
  input[type=range].tb-range{{width:55px;}}
  #scroll-area{{padding:10px;}}
  #statusbar{{font-size:10px;gap:8px;}}
  .sb-item{{padding-right:8px;}}
}}
</style>
</head>
<body>
<div id="panel-overlay" onclick="togglePanel(false)"></div>
<div id="app">

  <!-- 리본 툴바 -->
  <div id="ribbon">
    <!-- 도구 그룹 -->
    <div class="tb-group">
      <div class="tb-row">
        <button class="tb-btn active" id="btn-line" onclick="setTool('line')" title="두 점을 클릭하여 거리 측정">📏 선분 측정</button>
        <button class="tb-btn" id="btn-ray" onclick="setTool('ray')" title="두 점을 통과하는 무한 직선 (소실점 탐색용)">🗾 직선</button>
        <button class="tb-btn" id="btn-erase" onclick="setTool('erase')" title="클릭으로 선 삭제">🗑️ 지우개</button>
      </div>
      <div class="tb-group-label">도구</div>
    </div>

    <!-- 색상/두께 그룹 -->
    <div class="tb-group">
      <div class="tb-row">
        <span class="tb-label">색:</span>
        <input type="color" class="tb-color" id="line-color" value="{line_color}" onchange="lineColor=this.value" title="선 색상">
        <span class="tb-label" style="margin-left:4px;">두께:</span>
        <input type="range" class="tb-range" id="lw-range" min="1" max="10" value="{line_width}"
               oninput="lineWidth=+this.value;document.getElementById('lw-val').textContent=this.value">
        <span id="lw-val" style="font-size:11px;min-width:14px;">{line_width}</span>
      </div>
      <div class="tb-group-label">선 스타일</div>
    </div>

    <!-- 단위 그룹 -->
    <div class="tb-group">
      <div class="tb-row">
        <span class="tb-label">단위:</span>
        <select class="tb-select" id="unit-sel" onchange="changeUnit(this.value)">
          <option value="px" {unit_sel_px}>픽셀 (px)</option>
          <option value="cm" {unit_sel_cm}>센티미터 (cm)</option>
        </select>
        <span class="tb-label" id="dpi-lbl" style="display:{dpi_label_display};margin-left:6px;">DPI:</span>
        <input type="number" class="tb-num" id="dpi-in" value="{dpi}" min="1" max="1200"
               style="display:{dpi_display};"
               onchange="dpiVal=+this.value;refreshAll()">
      </div>
      <div class="tb-group-label">측정 단위</div>
    </div>

    <!-- 줌 그룹 -->
    <div class="tb-group">
      <div class="tb-row">
        <button class="tb-btn" onclick="changeZoom(-0.25)" title="축소 (-)">－</button>
        <span id="zoom-lbl" style="font-size:11px;min-width:38px;text-align:center;">100%</span>
        <button class="tb-btn" onclick="changeZoom(+0.25)" title="확대 (+)">＋</button>
        <button class="tb-btn" onclick="fitZoom()" title="화면에 맞춤">맞춤</button>
      </div>
      <div class="tb-group-label">확대/축소</div>
    </div>

    <!-- 외부 여백 그룹 -->
    <div class="tb-group">
      <div class="tb-row">
        <span class="tb-label">외부 여백:</span>
        <input type="number" class="tb-num" id="pad-in" value="400" min="50" max="9999" step="100"
               style="width:66px;"
               onchange="changePAD(this.value)"
               title="이미지 외부 여백(px) — 소실점 등 이미지 바깥 측정에 활용">
        <span class="tb-label">px</span>
      </div>
      <div class="tb-group-label">외부 여백</div>
    </div>

    <!-- 편집 그룹 -->
    <div class="tb-group">
      <div class="tb-row">
        <button class="tb-btn" onclick="clearAll()" style="color:#b00" title="모든 측정선 삭제">🗑️ 전체 삭제</button>
        <button class="tb-btn" onclick="copyResult()" title="측정 결과 복사">📋 복사</button>
      </div>
      <div class="tb-group-label">편집</div>
    </div>

    <!-- 저장 그룹 -->
    <div class="tb-group">
      <div class="tb-row">
        <button class="tb-btn" onclick="saveImage()" title="선분·직선이 포함된 이미지를 PNG 파일로 저장 (선분 전체가 포함되도록 크롭)">📷 이미지 저장</button>
      </div>
      <div class="tb-group-label">저장</div>
    </div>

    <!-- 모바일 결과 패널 토글 (태블릿/폰에서만 표시) -->
    <div class="tb-group" style="margin-left:auto;">
      <div class="tb-row">
        <button class="tb-btn" id="panel-toggle"
                style="display:none;background:linear-gradient(to bottom,#e8f4e8,#c8e8c8);border-color:#66aa66;"
                onclick="togglePanel()" title="측정 결과 패널 열기/닫기">📐 결과</button>
      </div>
      <div class="tb-group-label" style="font-size:9px;color:#66aa66;">결과패널</div>
    </div>
  </div>

  <!-- 작업 영역 -->
  <div id="work-area">

    <!-- 스크롤 캔버스 -->
    <div id="scroll-area">
      <div id="canvas-container">
        <canvas id="bg-canvas"></canvas>
        <canvas id="draw-canvas"
          onmousedown="onDown(event)"
          onmousemove="onMove(event)"
          oncontextmenu="onRightClick(event);return false;"
        ></canvas>
      </div>
    </div>

    <!-- 측정 결과 패널 -->
    <div id="result-panel">
      <div id="result-header">📐 측정 결과</div>
      <div id="result-body">
        <div id="result-list"></div>
        <div id="result-empty">캔버스에서 두 점을 클릭하여<br>거리를 측정하세요<br><br>
          <span style="font-size:10px;color:#ccc;">이미지 외부에서도 측정 가능합니다</span>
        </div>
      </div>
      <div id="result-summary" id="rsummary"></div>
    </div>
  </div>

  <!-- 상태바 -->
  <div id="statusbar">
    <span class="sb-item" id="sb-cursor">커서: —</span>
    <span class="sb-item" id="sb-size">이미지: {img_w} × {img_h} px</span>
    <span class="sb-item" id="sb-unit">단위: {unit}</span>
    <span class="sb-item" id="sb-tool">도구: 선 측정</span>
    <span class="sb-item" id="sb-count">측정: 0 개</span>
    <span class="sb-item" id="sb-hint">첫 번째 점을 클릭하세요</span>
  </div>
</div>

<script>
// ── 상수 ──
const IMG_W = {img_w};
const IMG_H = {img_h};
const IMG_SRC = "data:{mime};base64,{img_b64}";
let PAD = 400;  // 이미지 외부 여백 (런타임에 변경 가능)

// ── 상태 ──
let zoom = 1.0;
let tool = 'line';
let lineColor = '{line_color}';
let lineWidth = {line_width};
let unitMode = '{unit}';
let dpiVal = {dpi};
let measurements = [];
let infiniteLines = [];  // 직선 목록 (결과 패널에 미표시)
let startPt = null;
let previewPt = null;
let hoveredIdx = -1;
let hoveredIL = -1;  // 호버된 직선 인덱스

// ── 요소 ──
const bgCanvas = document.getElementById('bg-canvas');
const drawCanvas = document.getElementById('draw-canvas');
const bgCtx = bgCanvas.getContext('2d');
const drawCtx = drawCanvas.getContext('2d');
const container = document.getElementById('canvas-container');

// (제목바 제거됨)

// ── 이미지 로드 ──
const img = new Image();
img.onload = () => {{ initZoom(); }};
img.src = IMG_SRC;

function initZoom() {{
  const sa = document.getElementById('scroll-area');
  const avW = sa.clientWidth - 40;
  const avH = sa.clientHeight - 40;
  const z = Math.min(avW / (IMG_W + PAD*2), avH / (IMG_H + PAD*2), 1.0);
  setZoom(Math.max(0.15, z));
}}

function setZoom(z) {{
  zoom = Math.max(0.1, Math.min(8, z));
  const cw = Math.round((IMG_W + PAD*2) * zoom);
  const ch = Math.round((IMG_H + PAD*2) * zoom);
  [bgCanvas, drawCanvas].forEach(c => {{ c.width = cw; c.height = ch; }});
  container.style.width = cw + 'px';
  container.style.height = ch + 'px';
  document.getElementById('zoom-lbl').textContent = Math.round(zoom*100) + '%';
  redraw();
}}

function canvasToImg(cx, cy) {{
  return {{ x: (cx/zoom) - PAD, y: (cy/zoom) - PAD }};
}}
function imgToCanvas(ix, iy) {{
  return {{ x: (ix + PAD) * zoom, y: (iy + PAD) * zoom }};
}}

// ── 그리기 ──
function redraw() {{
  const cw = bgCanvas.width, ch = bgCanvas.height;

  // 배경 (회색 — 이미지 외부)
  bgCtx.fillStyle = '#808080';
  bgCtx.fillRect(0, 0, cw, ch);

  // 흰 캔버스 영역 (이미지 영역)
  const ix = PAD * zoom, iy = PAD * zoom;
  const iw = IMG_W * zoom, ih = IMG_H * zoom;
  bgCtx.fillStyle = '#ffffff';
  bgCtx.fillRect(ix, iy, iw, ih);

  // 이미지
  bgCtx.drawImage(img, ix, iy, iw, ih);

  // 이미지 테두리
  bgCtx.strokeStyle = '#555';
  bgCtx.lineWidth = 1;
  bgCtx.strokeRect(ix, iy, iw, ih);

  // 측정선
  drawCtx.clearRect(0, 0, cw, ch);
  infiniteLines.forEach((l, i) => drawInfiniteLine(l, i === hoveredIL));
  measurements.forEach((m, i) => drawLine(m, i, i === hoveredIdx));

  // 미리보기 — startPt는 월드 좌표이므로 캔버스 좌표로 변환
  if (startPt && previewPt && (tool === 'line' || tool === 'ray')) {{
    const sp = {{ x: (startPt.x + PAD) * zoom, y: (startPt.y + PAD) * zoom }};
    drawCtx.save();
    drawCtx.strokeStyle = lineColor;
    drawCtx.lineWidth = lineWidth;
    if (tool === 'ray') {{
      const ext = extendToCanvas(sp.x, sp.y, previewPt.x, previewPt.y, cw, ch);
      drawCtx.setLineDash([8, 4]);
      drawCtx.globalAlpha = 0.65;
      drawCtx.beginPath();
      drawCtx.moveTo(ext.x1, ext.y1);
      drawCtx.lineTo(ext.x2, ext.y2);
      drawCtx.stroke();
    }} else {{
      drawCtx.setLineDash([6, 4]);
      drawCtx.globalAlpha = 0.75;
      drawCtx.beginPath();
      drawCtx.moveTo(sp.x, sp.y);
      drawCtx.lineTo(previewPt.x, previewPt.y);
      drawCtx.stroke();
    }}
    drawCtx.setLineDash([]);
    drawCtx.globalAlpha = 1;
    drawCtx.fillStyle = lineColor;
    drawCtx.beginPath();
    drawCtx.arc(sp.x, sp.y, 5, 0, Math.PI*2);
    drawCtx.fill();
    drawCtx.strokeStyle = 'white';
    drawCtx.lineWidth = 1.5;
    drawCtx.stroke();
    drawCtx.restore();
  }}

  updateStatusbar();
}}

// 캔버스 경계로 직선을 연장— (x1,y1)에서 (x2,y2) 방향 양쪽
function extendToCanvas(x1, y1, x2, y2, cw, ch) {{
  const dx = x2 - x1, dy = y2 - y1;
  if (dx === 0 && dy === 0) return {{ x1, y1, x2, y2 }};
  const ts = [];
  if (dx !== 0) {{ ts.push(-x1/dx); ts.push((cw-x1)/dx); }}
  if (dy !== 0) {{ ts.push(-y1/dy); ts.push((ch-y1)/dy); }}
  const neg = Math.min(...ts.filter(t => t <= 0));
  const pos = Math.max(...ts.filter(t => t >= 0));
  return {{
    x1: x1 + dx * (isFinite(neg) ? neg : -1e6),
    y1: y1 + dy * (isFinite(neg) ? neg : -1e6),
    x2: x1 + dx * (isFinite(pos) ? pos :  1e6),
    y2: y1 + dy * (isFinite(pos) ? pos :  1e6),
  }};
}}

function drawInfiniteLine(l, hovered) {{
  const cw = drawCanvas.width, ch = drawCanvas.height;
  const cx1 = (l.wx1 + PAD) * zoom;
  const cy1 = (l.wy1 + PAD) * zoom;
  const cx2 = (l.wx2 + PAD) * zoom;
  const cy2 = (l.wy2 + PAD) * zoom;
  const ext = extendToCanvas(cx1, cy1, cx2, cy2, cw, ch);

  drawCtx.save();
  drawCtx.strokeStyle = l.color || '#0088ff';
  drawCtx.lineWidth = (l.lw || 2) + (hovered ? 2 : 0);
  if (hovered) {{
    drawCtx.shadowColor = 'rgba(255,220,0,0.9)';
    drawCtx.shadowBlur = 10;
  }} else {{
    drawCtx.shadowColor = 'rgba(0,0,0,0.3)';
    drawCtx.shadowBlur = 3;
  }}
  drawCtx.beginPath();
  drawCtx.moveTo(ext.x1, ext.y1);
  drawCtx.lineTo(ext.x2, ext.y2);
  drawCtx.stroke();
  drawCtx.shadowBlur = 0;

  // 지정점 두 개 표시
  [{{x:cx1,y:cy1}},{{x:cx2,y:cy2}}].forEach(p => {{
    drawCtx.fillStyle = l.color || '#0088ff';
    drawCtx.beginPath();
    drawCtx.arc(p.x, p.y, 4, 0, Math.PI*2);
    drawCtx.fill();
    drawCtx.strokeStyle = 'white';
    drawCtx.lineWidth = 1.5;
    drawCtx.stroke();
  }});
  drawCtx.restore();
}}

function drawLine(m, idx, hovered) {{
  // 월드 좌표 → 캔버스 좌표 변환
  const cx1 = (m.wx1 + PAD) * zoom;
  const cy1 = (m.wy1 + PAD) * zoom;
  const cx2 = (m.wx2 + PAD) * zoom;
  const cy2 = (m.wy2 + PAD) * zoom;

  const lw = (m.lw || 2) + (hovered ? 2 : 0);
  drawCtx.save();
  drawCtx.strokeStyle = m.color || '#ff0000';
  drawCtx.lineWidth = lw;
  if (hovered) {{
    drawCtx.shadowColor = 'rgba(255,220,0,0.9)';
    drawCtx.shadowBlur = 10;
  }} else {{
    drawCtx.shadowColor = 'rgba(0,0,0,0.35)';
    drawCtx.shadowBlur = 3;
  }}
  drawCtx.beginPath();
  drawCtx.moveTo(cx1, cy1);
  drawCtx.lineTo(cx2, cy2);
  drawCtx.stroke();
  drawCtx.shadowBlur = 0;

  // 끝점
  [{{x:cx1,y:cy1}},{{x:cx2,y:cy2}}].forEach(p => {{
    drawCtx.fillStyle = m.color || '#ff0000';
    drawCtx.beginPath();
    drawCtx.arc(p.x, p.y, 5, 0, Math.PI*2);
    drawCtx.fill();
    drawCtx.strokeStyle = 'white';
    drawCtx.lineWidth = 1.5;
    drawCtx.stroke();
  }});

  // 라벨
  const mx = (cx1+cx2)/2, my = (cy1+cy2)/2;
  const label = (idx+1) + ': ' + fmtLen(m.lenPx);
  drawCtx.font = 'bold 12px Segoe UI,sans-serif';
  const tw = drawCtx.measureText(label).width;
  drawCtx.fillStyle = 'rgba(255,255,255,0.9)';
  drawCtx.fillRect(mx-tw/2-5, my-11, tw+10, 19);
  drawCtx.strokeStyle = m.color || '#ff0000';
  drawCtx.lineWidth = 1;
  drawCtx.strokeRect(mx-tw/2-5, my-11, tw+10, 19);
  drawCtx.fillStyle = '#111';
  drawCtx.textAlign = 'center';
  drawCtx.textBaseline = 'middle';
  drawCtx.fillText(label, mx, my);
  drawCtx.restore();
}}

// ── 거리 계산 (월드/이미지 픽셀 기준 — zoom 무관) ──
function calcDist(wx1,wy1,wx2,wy2) {{
  const dx=wx2-wx1, dy=wy2-wy1;
  return Math.sqrt(dx*dx+dy*dy);
}}

function fmtLen(px) {{
  if (unitMode==='cm') {{
    const ppcm = dpiVal/2.54;
    return (px/ppcm).toFixed(2)+' cm';
  }}
  return px.toFixed(1)+' px';
}}

// ── 마우스 이벤트 ──
function getPos(e) {{
  const r = drawCanvas.getBoundingClientRect();
  const sx = drawCanvas.width/r.width;
  const sy = drawCanvas.height/r.height;
  return {{x:(e.clientX-r.left)*sx, y:(e.clientY-r.top)*sy}};
}}

function onDown(e) {{
  if (e.button!==0) return;
  const cp = getPos(e);
  // 캔버스 좌표 → 월드 좌표 (이미지 기준, 외부는 음수 가능)
  const wp = {{ x: cp.x/zoom - PAD, y: cp.y/zoom - PAD }};
  if (tool==='line') {{
    if (!startPt) {{
      startPt = wp;
      previewPt = cp;
      document.getElementById('sb-hint').textContent = '두 번째 점 클릭 (우클릭: 취소)';
    }} else {{
      const px = calcDist(startPt.x,startPt.y,wp.x,wp.y);
      measurements.push({{
        wx1:startPt.x, wy1:startPt.y,
        wx2:wp.x, wy2:wp.y,
        lenPx:px,
        color:lineColor,
        lw:lineWidth,
      }});
      startPt=null; previewPt=null;
      updatePanel();
      document.getElementById('sb-hint').textContent = '첫 번째 점을 클릭하세요';
      redraw();
    }}
  }} else if (tool==='ray') {{
    if (!startPt) {{
      startPt = wp;
      previewPt = cp;
      document.getElementById('sb-hint').textContent = '두 번째 점 클릭 (우클릭: 취소)';
    }} else {{
      infiniteLines.push({{
        wx1:startPt.x, wy1:startPt.y,
        wx2:wp.x, wy2:wp.y,
        color:lineColor,
        lw:lineWidth,
      }});
      startPt=null; previewPt=null;
      document.getElementById('sb-hint').textContent = '첫 번째 점을 클릭하세요';
      redraw();
    }}
  }} else if (tool==='erase') {{
    // 선분 먼저, 없으면 직선 확인
    let idx = nearestLine(cp.x,cp.y,18);
    if (idx>=0) {{
      measurements.splice(idx,1);
      updatePanel();
      redraw();
    }} else {{
      const il = nearestIL(cp.x,cp.y,18);
      if (il>=0) {{ infiniteLines.splice(il,1); redraw(); }}
    }}
  }}
}}

function onMove(e) {{
  const p = getPos(e);
  const ip = canvasToImg(p.x, p.y);
  const coordStr = unitMode==='cm'
    ? `(${{(ip.x/(dpiVal/2.54)).toFixed(2)}},${{(ip.y/(dpiVal/2.54)).toFixed(2)}} cm)`
    : `(${{Math.round(ip.x)}},${{Math.round(ip.y)}} px)`;
  document.getElementById('sb-cursor').textContent = '커서: '+coordStr;

  if (tool==='line' && startPt) {{
    previewPt = p;
    const wp2 = {{ x: p.x/zoom - PAD, y: p.y/zoom - PAD }};
    const px = calcDist(startPt.x,startPt.y,wp2.x,wp2.y);
    document.getElementById('sb-hint').textContent = '현재: '+fmtLen(px);
    redraw();
  }}
  if (tool==='ray' && startPt) {{
    previewPt = p;
    redraw();
  }}
  if (tool==='erase') {{
    const prev = hoveredIdx, prevIL = hoveredIL;
    hoveredIdx = nearestLine(p.x,p.y,18);
    hoveredIL = hoveredIdx < 0 ? nearestIL(p.x,p.y,18) : -1;
    if (hoveredIdx!==prev || hoveredIL!==prevIL) redraw();
  }}
}}

function onRightClick(e) {{
  if (startPt) {{
    startPt=null; previewPt=null;
    document.getElementById('sb-hint').textContent = '첫 번째 점을 클릭하세요';
    redraw();
  }}
}}

// 직선에 가장 가까운 선 찾기 (점-직선 거리)
function nearestIL(cx,cy,thresh) {{
  let best=-1, bd=thresh;
  infiniteLines.forEach((l,i) => {{
    const lx1=(l.wx1+PAD)*zoom, ly1=(l.wy1+PAD)*zoom;
    const lx2=(l.wx2+PAD)*zoom, ly2=(l.wy2+PAD)*zoom;
    const d = ptLineDist(cx,cy,lx1,ly1,lx2,ly2);
    if(d<bd){{bd=d;best=i;}}
  }});
  return best;
}}

function ptLineDist(px,py,x1,y1,x2,y2) {{
  const dx=x2-x1, dy=y2-y1;
  if(dx===0&&dy===0) return Math.hypot(px-x1,py-y1);
  return Math.abs(dy*px - dx*py + x2*y1 - y2*x1) / Math.hypot(dx,dy);
}}

function nearestLine(cx,cy,thresh) {{
  // cx,cy는 캔버스 좌표 — 측정값(월드)을 캔버스로 변환해 비교
  let best=-1, bd=thresh;
  measurements.forEach((m,i) => {{
    const mx1=(m.wx1+PAD)*zoom, my1=(m.wy1+PAD)*zoom;
    const mx2=(m.wx2+PAD)*zoom, my2=(m.wy2+PAD)*zoom;
    const d = ptSegDist(cx,cy,mx1,my1,mx2,my2);
    if(d<bd){{bd=d;best=i;}}
  }});
  return best;
}}

function ptSegDist(px,py,x1,y1,x2,y2) {{
  const dx=x2-x1,dy=y2-y1,l2=dx*dx+dy*dy;
  if(l2===0) return Math.hypot(px-x1,py-y1);
  const t=Math.max(0,Math.min(1,((px-x1)*dx+(py-y1)*dy)/l2));
  return Math.hypot(px-(x1+t*dx),py-(y1+t*dy));
}}

// ── 도구 ──
function setTool(t) {{
  tool=t; startPt=null; previewPt=null;
  document.querySelectorAll('.tb-btn[id^="btn-"]').forEach(b=>b.classList.remove('active'));
  document.getElementById('btn-'+t).classList.add('active');
  const names={{line:'선분 측정',ray:'직선',erase:'지우개'}};
  document.getElementById('sb-tool').textContent='도구: '+names[t];
  const hints={{line:'첫 번째 점을 클릭하세요',ray:'첫 번째 점을 클릭하세요',erase:'지울 선을 클릭하세요'}};
  document.getElementById('sb-hint').textContent=hints[t];
  if(t==='erase'){{hoveredIdx=-1;hoveredIL=-1;redraw();}}
}}

// ── 단위/DPI ──
function changeUnit(v) {{
  unitMode=v;
  document.getElementById('dpi-lbl').style.display=v==='cm'?'inline':'none';
  document.getElementById('dpi-in').style.display=v==='cm'?'inline-block':'none';
  refreshAll();
}}
function refreshAll() {{
  updatePanel();
  redraw();
}}

// ── 줌 ──
function changeZoom(d) {{ setZoom(zoom+d); }}
function fitZoom() {{
  const sa=document.getElementById('scroll-area');
  const avW=sa.clientWidth-40, avH=sa.clientHeight-40;
  setZoom(Math.max(0.1,Math.min(avW/(IMG_W+PAD*2),avH/(IMG_H+PAD*2),1.0)));
}}

// ── 외부 여백 변경 ──
function changePAD(v) {{
  PAD = Math.max(50, parseInt(v) || 400);
  setZoom(zoom);  // 캔버스 크기 재계산 후 redraw
}}

// ── 전체 삭제 ──
function clearAll() {{
  if(!measurements.length && !infiniteLines.length) return;
  if(!confirm('모든 선을 삭제하시겠습니까?')) return;
  measurements=[]; infiniteLines=[]; startPt=null; previewPt=null;
  updatePanel(); redraw();
}}

// ── 결과 복사 ──
function copyResult() {{
  if(!measurements.length){{alert('측정 결과가 없습니다.');return;}}
  const lines=measurements.map((m,i)=>
    (i+1)+'. '+fmtLen(m.lenPx)
  );
  const text='=== 측정 결과 ===\\n'+lines.join('\\n');
  navigator.clipboard.writeText(text).then(
    ()=>alert('클립보드에 복사되었습니다!\\n\\n'+text),
    ()=>prompt('아래를 복사하세요:',text)
  );
}}

// ── 결과 패널 ──
function updatePanel() {{
  const list=document.getElementById('result-list');
  const empty=document.getElementById('result-empty');
  const summary=document.getElementById('result-summary');
  document.getElementById('sb-count').textContent='측정: '+measurements.length+' 개';

  if(!measurements.length) {{
    list.innerHTML=''; empty.style.display='block'; summary.textContent='';
    return;
  }}
  empty.style.display='none';
  list.innerHTML=measurements.map((m,i)=>{{
    // 월드 좌표를 직접 표시 (이미지 기준, 외부는 음수)
    const coordTxt=`(${{Math.round(m.wx1)}}, ${{Math.round(m.wy1)}}) → (${{Math.round(m.wx2)}}, ${{Math.round(m.wy2)}})`;
    return `<div class="r-row"
      onmouseenter="hoveredIdx=${{i}};redraw();"
      onmouseleave="hoveredIdx=-1;redraw();">
      <span class="r-num" style="background:${{m.color||'#3366cc'}}">${{i+1}}</span>
      <div class="r-info">
        <div class="r-len">${{fmtLen(m.lenPx)}}</div>
        <div class="r-coord">${{coordTxt}}</div>
      </div>
      <button class="r-del" onclick="delLine(${{i}})" title="삭제">✕</button>
    </div>`;
  }}).join('');

  if(measurements.length>1) {{
    const total=measurements.reduce((s,m)=>s+m.lenPx,0);
    summary.innerHTML=`<b>합계:</b> ${{fmtLen(total)}} &nbsp;|&nbsp; <b>평균:</b> ${{fmtLen(total/measurements.length)}}`;
  }} else {{
    summary.textContent='';
  }}
}}

function delLine(i) {{
  measurements.splice(i,1); updatePanel(); redraw();
}}

// ── 상태바 업데이트 ──
function updateStatusbar() {{
  const u=unitMode;
  const wStr=u==='cm'?(IMG_W/(dpiVal/2.54)).toFixed(1)+' cm':IMG_W+' px';
  const hStr=u==='cm'?(IMG_H/(dpiVal/2.54)).toFixed(1)+' cm':IMG_H+' px';
  document.getElementById('sb-size').textContent='이미지: '+wStr+' × '+hStr;
  document.getElementById('sb-unit').textContent='단위: '+(u==='cm'?'cm':'px');
}}

updatePanel();

// ── 터치 이벤트 (모바일 지원) ──
(function() {{
  let longPressTimer = null;
  let touchMoved = false;
  const LONG_PRESS_MS = 600;

  function touchPos(touch) {{
    // 실제 캔버스 좌표로 변환 (getBoundingClientRect 사용)
    return {{ clientX: touch.clientX, clientY: touch.clientY, button: 0 }};
  }}

  drawCanvas.addEventListener('touchstart', e => {{
    e.preventDefault();
    touchMoved = false;
    const t = e.touches[0];
    const ev = touchPos(t);
    // 롱프레스 → 취소 (우클릭 대체)
    longPressTimer = setTimeout(() => {{
      if (!touchMoved) {{
        onRightClick(ev);
        // 진동 피드백 (지원 기기)
        if (navigator.vibrate) navigator.vibrate(30);
      }}
    }}, LONG_PRESS_MS);
    onDown(ev);
  }}, {{passive: false}});

  drawCanvas.addEventListener('touchmove', e => {{
    e.preventDefault();
    touchMoved = true;
    clearTimeout(longPressTimer);
    const t = e.touches[0];
    onMove(touchPos(t));
  }}, {{passive: false}});

  drawCanvas.addEventListener('touchend', e => {{
    clearTimeout(longPressTimer);
  }}, {{passive: true}});

  drawCanvas.addEventListener('touchcancel', e => {{
    clearTimeout(longPressTimer);
  }}, {{passive: true}});
}})();

// ── 모바일 결과 패널 토글 ──
function togglePanel(forceOpen) {{
  const panel = document.getElementById('result-panel');
  const overlay = document.getElementById('panel-overlay');
  const isOpen = panel.classList.contains('open');
  const open = forceOpen !== undefined ? forceOpen : !isOpen;
  panel.classList.toggle('open', open);
  overlay.classList.toggle('open', open);
}}

// ── 이미지 저장 ──
function saveImage() {{
  // 바운딩 박스 (세계 좌표, 이미지 기준) — 최소한 이미지 전체 포함
  let minX = 0, minY = 0, maxX = IMG_W, maxY = IMG_H;
  measurements.forEach(m => {{
    minX = Math.min(minX, m.wx1, m.wx2);
    minY = Math.min(minY, m.wy1, m.wy2);
    maxX = Math.max(maxX, m.wx1, m.wx2);
    maxY = Math.max(maxY, m.wy1, m.wy2);
  }});
  infiniteLines.forEach(l => {{
    minX = Math.min(minX, l.wx1, l.wx2);
    minY = Math.min(minY, l.wy1, l.wy2);
    maxX = Math.max(maxX, l.wx1, l.wx2);
    maxY = Math.max(maxY, l.wy1, l.wy2);
  }});
  const M = 50;
  minX -= M; minY -= M; maxX += M; maxY += M;
  const W = Math.round(maxX - minX), H = Math.round(maxY - minY);

  const oc = document.createElement('canvas');
  oc.width = W; oc.height = H;
  const ctx = oc.getContext('2d');

  // 배경 (이미지 외부 = 회색)
  ctx.fillStyle = '#808080';
  ctx.fillRect(0, 0, W, H);

  // 이미지 영역 (흰 배경 + 실제 이미지)
  const ox = -minX, oy = -minY;
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(ox, oy, IMG_W, IMG_H);
  ctx.drawImage(img, ox, oy, IMG_W, IMG_H);
  ctx.strokeStyle = '#555'; ctx.lineWidth = 1;
  ctx.strokeRect(ox, oy, IMG_W, IMG_H);

  // 직선 그리기 (캔버스 전체로 연장)
  infiniteLines.forEach(l => {{
    const cx1 = l.wx1 + ox, cy1 = l.wy1 + oy;
    const cx2 = l.wx2 + ox, cy2 = l.wy2 + oy;
    const ext = extendToCanvas(cx1, cy1, cx2, cy2, W, H);
    ctx.save();
    ctx.strokeStyle = l.color || '#0088ff';
    ctx.lineWidth = l.lw || 2;
    ctx.shadowColor = 'rgba(0,0,0,0.3)'; ctx.shadowBlur = 3;
    ctx.beginPath(); ctx.moveTo(ext.x1, ext.y1); ctx.lineTo(ext.x2, ext.y2); ctx.stroke();
    ctx.shadowBlur = 0;
    [{{x:cx1,y:cy1}},{{x:cx2,y:cy2}}].forEach(p => {{
      ctx.fillStyle = l.color||'#0088ff';
      ctx.beginPath(); ctx.arc(p.x,p.y,4,0,Math.PI*2); ctx.fill();
      ctx.strokeStyle='white'; ctx.lineWidth=1.5; ctx.stroke();
    }});
    ctx.restore();
  }});

  // 선분 + 길이 라벨 그리기
  measurements.forEach((m, i) => {{
    const cx1 = m.wx1 + ox, cy1 = m.wy1 + oy;
    const cx2 = m.wx2 + ox, cy2 = m.wy2 + oy;
    ctx.save();
    ctx.strokeStyle = m.color||'#ff0000'; ctx.lineWidth = m.lw||2;
    ctx.shadowColor='rgba(0,0,0,0.35)'; ctx.shadowBlur=3;
    ctx.beginPath(); ctx.moveTo(cx1,cy1); ctx.lineTo(cx2,cy2); ctx.stroke();
    ctx.shadowBlur=0;
    [{{x:cx1,y:cy1}},{{x:cx2,y:cy2}}].forEach(p => {{
      ctx.fillStyle = m.color||'#ff0000';
      ctx.beginPath(); ctx.arc(p.x,p.y,5,0,Math.PI*2); ctx.fill();
      ctx.strokeStyle='white'; ctx.lineWidth=1.5; ctx.stroke();
    }});
    // 라벨
    const mx=(cx1+cx2)/2, my=(cy1+cy2)/2;
    const label=(i+1)+': '+fmtLen(m.lenPx);
    ctx.font='bold 14px "Segoe UI",sans-serif';
    const tw=ctx.measureText(label).width;
    ctx.fillStyle='rgba(255,255,255,0.92)';
    ctx.fillRect(mx-tw/2-7,my-14,tw+14,24);
    ctx.strokeStyle=m.color||'#ff0000'; ctx.lineWidth=1.5;
    ctx.strokeRect(mx-tw/2-7,my-14,tw+14,24);
    ctx.fillStyle='#111'; ctx.textAlign='center'; ctx.textBaseline='middle';
    ctx.fillText(label,mx,my);
    ctx.restore();
  }});

  oc.toBlob(blob => {{
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href=url; a.download='measurement_'+Date.now()+'.png';
    document.body.appendChild(a); a.click();
    document.body.removeChild(a); URL.revokeObjectURL(url);
  }}, 'image/png');
}}
</script>
</body>
</html>
"""

    st.components.v1.html(canvas_html, height=920, scrolling=False)


# ──────────────────────────────────────────────────────────────────────────────
# 계산기 (항상 표시)
# ──────────────────────────────────────────────────────────────────────────────
calc_html = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: #ece9d8;
  font-family: 'Segoe UI', Tahoma, sans-serif;
  overflow: hidden;
  height: 100vh;
  display: flex;
  flex-direction: column;
}
#calc-title {
  background: linear-gradient(to bottom, #f0f0f0, #e0e0e0);
  border-bottom: 2px solid #b0b0b0;
  padding: 4px 12px;
  font-size: 12px; font-weight: bold; color: #333;
  flex-shrink: 0;
}
#calc-wrap {
  display: flex;
  flex: 1;
  gap: 8px;
  padding: 8px;
  overflow: hidden;
}
/* ── 계산기 패널 ── */
#calc-panel {
  width: 310px;
  min-width: 310px;
  background: #ffffff;
  border: 1px solid #c0c0c0;
  border-radius: 6px;
  box-shadow: 2px 2px 8px rgba(0,0,0,0.15);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
/* 디스플레이 */
#calc-display {
  background: #1c1c1c;
  padding: 8px 14px 8px;
  text-align: right;
  min-height: 72px;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
}
#calc-expr {
  color: #888; font-size: 11px;
  min-height: 16px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
#calc-screen {
  color: white; font-size: 34px; font-weight: 300;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  cursor: default;
}
/* 메모리 바 */
#calc-mem {
  display: flex;
  background: #f7f7f7;
  border-bottom: 1px solid #e0e0e0;
  padding: 1px 2px;
  gap: 1px;
}
#calc-mem button {
  flex: 1; background: none; border: none;
  padding: 5px 2px; font-size: 11px; color: #555; cursor: pointer;
  border-radius: 3px;
}
#calc-mem button:hover { background: #e0e0e0; }
/* 버튼 그리드 */
#calc-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 2px; padding: 3px;
  flex: 1;
}
.cb {
  background: #f9f9f9; border: none;
  font-size: 15px; cursor: pointer;
  border-radius: 3px; color: #1c1c1c;
  transition: background 0.08s;
  min-height: 42px;
}
.cb:hover { background: #e4e4e4; }
.cb:active { background: #ccc; }
.cb.op { background: #f0f0f0; font-size: 17px; }
.cb.op:hover { background: #ddd; }
.cb.eq { background: #0078d4; color: white; font-size: 19px; font-weight: 500; }
.cb.eq:hover { background: #106ebe; }
.cb.eq:active { background: #005a9e; }
.cb.sp { background: #f5f5f5; font-size: 12px; color: #333; }
.cb.sp:hover { background: #e4e4e4; }
.cb.del-btn { color: #c00; }
/* ── 이력 패널 ── */
#history-panel {
  flex: 1;
  background: white;
  border: 1px solid #c0c0c0;
  border-radius: 6px;
  box-shadow: 2px 2px 8px rgba(0,0,0,0.1);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
#history-header {
  background: linear-gradient(to bottom, #f0f0f0, #e4e4e4);
  border-bottom: 1px solid #d0d0d0;
  padding: 6px 10px;
  font-size: 12px; font-weight: bold; color: #333;
  display: flex; justify-content: space-between; align-items: center;
  flex-shrink: 0;
}
#history-header button {
  background: none; border: 1px solid #c0c0c0; border-radius: 3px;
  padding: 1px 8px; font-size: 11px; cursor: pointer; color: #c00;
}
#history-header button:hover { background: #ffe0e0; }
#history-body {
  flex: 1; overflow-y: auto; padding: 4px;
}
.h-item {
  padding: 7px 10px;
  border-bottom: 1px solid #f0f0f0;
  font-size: 13px; color: #333;
  text-align: right; cursor: pointer;
  word-break: break-all; line-height: 1.5;
}
.h-item:hover { background: #f0f4ff; }
.h-item .h-result { font-weight: bold; color: #0078d4; }
.h-empty {
  color: #bbb; font-size: 12px;
  text-align: center; padding: 40px 10px; line-height: 2;
}
</style>
</head>
<body>
<div id="calc-title">🔢 계산기</div>
<div id="calc-wrap">
  <!-- 계산기 -->
  <div id="calc-panel">
    <div id="calc-display">
      <div id="calc-expr"></div>
      <div id="calc-screen">0</div>
    </div>
    <div id="calc-mem">
      <button onclick="memClear()" title="메모리 지우기">MC</button>
      <button onclick="memRecall()" title="메모리 불러오기">MR</button>
      <button onclick="memAdd()" title="메모리에 더하기">M+</button>
      <button onclick="memSub()" title="메모리에서 빼기">M−</button>
      <button onclick="memStore()" title="메모리에 저장">MS</button>
    </div>
    <div id="calc-grid">
      <!-- 행 1 -->
      <button class="cb sp" onclick="percent()">%</button>
      <button class="cb sp" onclick="clearEntry()">CE</button>
      <button class="cb sp" onclick="allClear()">C</button>
      <button class="cb sp del-btn" onclick="backspace()">⌫</button>
      <!-- 행 2 -->
      <button class="cb sp" onclick="recip()" title="역수">¹/ₓ</button>
      <button class="cb sp" onclick="square()" title="제곱">x²</button>
      <button class="cb sp" onclick="sqrtFn()" title="제곱근">²√x</button>
      <button class="cb op" onclick="operator('/')">÷</button>
      <!-- 행 3 -->
      <button class="cb" onclick="digit('7')">7</button>
      <button class="cb" onclick="digit('8')">8</button>
      <button class="cb" onclick="digit('9')">9</button>
      <button class="cb op" onclick="operator('*')">×</button>
      <!-- 행 4 -->
      <button class="cb" onclick="digit('4')">4</button>
      <button class="cb" onclick="digit('5')">5</button>
      <button class="cb" onclick="digit('6')">6</button>
      <button class="cb op" onclick="operator('-')">−</button>
      <!-- 행 5 -->
      <button class="cb" onclick="digit('1')">1</button>
      <button class="cb" onclick="digit('2')">2</button>
      <button class="cb" onclick="digit('3')">3</button>
      <button class="cb op" onclick="operator('+')">+</button>
      <!-- 행 6 -->
      <button class="cb sp" onclick="toggleSign()">+/−</button>
      <button class="cb" onclick="digit('0')">0</button>
      <button class="cb" onclick="decimal()">.</button>
      <button class="cb eq" onclick="equals()">=</button>
    </div>
  </div>

  <!-- 계산 이력 -->
  <div id="history-panel">
    <div id="history-header">
      <span>📋 계산 이력</span>
      <button onclick="clearHistory()">이력 지우기</button>
    </div>
    <div id="history-body">
      <div class="h-empty" id="h-empty">계산 결과가 여기에 표시됩니다<br><span style="font-size:11px;color:#ccc;">이력을 클릭하면 결과를 불러옵니다</span></div>
      <div id="history-list"></div>
    </div>
  </div>
</div>

<script>
// ── 상태 ──
let curVal = '0';
let pendingOp = null;
let pendingVal = null;
let waitingForOperand = false;
let exprStr = '';
let memory = 0;
let calcHistory = [];
let hasError = false;

// ── 숫자 입력 ──
function digit(d) {
  if (hasError) return;
  if (waitingForOperand) {
    curVal = d; waitingForOperand = false;
  } else {
    if (curVal.replace(/[^0-9]/g,'').length >= 15) return;
    curVal = (curVal === '0') ? d : curVal + d;
  }
  updateDisplay();
}

function decimal() {
  if (hasError) return;
  if (waitingForOperand) { curVal = '0.'; waitingForOperand = false; }
  else if (!curVal.includes('.')) curVal += '.';
  updateDisplay();
}

// ── 연산자 ──
function operator(op) {
  if (hasError) return;
  const cur = parseFloat(curVal);
  if (pendingOp && !waitingForOperand) {
    const res = compute(pendingVal, cur, pendingOp);
    if (res === null) { showError('0으로 나눌 수 없습니다'); return; }
    curVal = fmt(res); pendingVal = res;
    exprStr = fmt(res) + ' ' + opSym(op);
  } else {
    pendingVal = isNaN(cur) ? 0 : cur;
    exprStr = fmt(pendingVal) + ' ' + opSym(op);
  }
  pendingOp = op; waitingForOperand = true;
  updateDisplay();
}

// ── 등호 ──
function equals() {
  if (hasError || pendingOp === null || pendingVal === null) return;
  const cur = parseFloat(curVal);
  const res = compute(pendingVal, cur, pendingOp);
  if (res === null) { showError('0으로 나눌 수 없습니다'); return; }
  const entry = {
    expr: fmt(pendingVal) + ' ' + opSym(pendingOp) + ' ' + fmt(cur),
    result: fmt(res)
  };
  calcHistory.unshift(entry);
  exprStr = entry.expr + ' =';
  curVal = fmt(res);
  pendingOp = null; pendingVal = null; waitingForOperand = true;
  updateDisplay(); updateHistory();
}

function compute(a, b, op) {
  switch(op) {
    case '+': return a + b;
    case '-': return a - b;
    case '*': return a * b;
    case '/': return b === 0 ? null : a / b;
    default: return b;
  }
}
function opSym(op) {
  return {'+':'+', '-':'−', '*':'×', '/':'÷'}[op] || op;
}
function fmt(n) {
  if (!isFinite(n)) return 'Error';
  if (Math.abs(n) >= 1e15) return n.toExponential(8);
  if (Number.isInteger(n)) return String(n);
  return String(parseFloat(n.toPrecision(12)));
}
function showError(msg) {
  curVal = msg || 'Error'; hasError = true;
  pendingOp = null; pendingVal = null; exprStr = '';
  updateDisplay();
}

// ── 지우기 ──
function clearEntry() { hasError = false; curVal = '0'; updateDisplay(); }
function allClear() {
  curVal = '0'; pendingOp = null; pendingVal = null;
  waitingForOperand = false; exprStr = ''; hasError = false;
  updateDisplay();
}
function backspace() {
  if (hasError || waitingForOperand) return;
  curVal = curVal.length > 1 ? curVal.slice(0,-1) : '0';
  if (curVal === '-') curVal = '0';
  updateDisplay();
}

// ── 부호/퍼센트 ──
function toggleSign() {
  if (hasError || curVal === '0') return;
  curVal = curVal.startsWith('-') ? curVal.slice(1) : '-' + curVal;
  updateDisplay();
}
function percent() {
  if (hasError) return;
  const v = parseFloat(curVal);
  const result = (pendingVal !== null && pendingOp && (pendingOp==='+' || pendingOp==='-'))
    ? pendingVal * v / 100
    : v / 100;
  curVal = fmt(result); waitingForOperand = false;
  updateDisplay();
}

// ── 단항 함수 ──
function recip() {
  if (hasError) return;
  const v = parseFloat(curVal);
  if (v === 0) { showError('0으로 나눌 수 없습니다'); return; }
  curVal = fmt(1/v); waitingForOperand = false; updateDisplay();
}
function square() {
  if (hasError) return;
  curVal = fmt(parseFloat(curVal) ** 2); waitingForOperand = false; updateDisplay();
}
function sqrtFn() {
  if (hasError) return;
  const v = parseFloat(curVal);
  if (v < 0) { showError('유효하지 않은 입력'); return; }
  curVal = fmt(Math.sqrt(v)); waitingForOperand = false; updateDisplay();
}

// ── 메모리 ──
function memClear()  { memory = 0; }
function memRecall() { curVal = fmt(memory); waitingForOperand = false; updateDisplay(); }
function memAdd()    { if (!hasError) memory += parseFloat(curVal); }
function memSub()    { if (!hasError) memory -= parseFloat(curVal); }
function memStore()  { if (!hasError) memory = parseFloat(curVal); }

// ── 이력 ──
function clearHistory() { calcHistory = []; updateHistory(); }

// ── 디스플레이 갱신 ──
function updateDisplay() {
  let disp = curVal;
  if (disp.length > 16) disp = parseFloat(parseFloat(curVal).toPrecision(10)).toString();
  document.getElementById('calc-screen').textContent = disp;
  document.getElementById('calc-expr').textContent = exprStr;
}
function updateHistory() {
  const list = document.getElementById('history-list');
  const empty = document.getElementById('h-empty');
  if (!calcHistory.length) { empty.style.display='block'; list.innerHTML=''; return; }
  empty.style.display = 'none';
  list.innerHTML = calcHistory.slice(0,100).map((h,i) =>
    `<div class="h-item" data-idx="${i}">${h.expr}<br><span class="h-result">= ${h.result}</span></div>`
  ).join('');
  list.querySelectorAll('.h-item').forEach(el => {
    el.addEventListener('click', () => {
      const idx = parseInt(el.dataset.idx);
      curVal = calcHistory[idx].result;
      waitingForOperand = false; updateDisplay();
    });
  });
}

// ── 키보드 ──
document.addEventListener('keydown', e => {
  if (document.activeElement.tagName === 'INPUT') return;
  if ('0123456789'.includes(e.key)) { digit(e.key); return; }
  switch(e.key) {
    case '.': decimal(); break;
    case '+': operator('+'); break;
    case '-': operator('-'); break;
    case '*': operator('*'); break;
    case '/': e.preventDefault(); operator('/'); break;
    case 'Enter': case '=': equals(); break;
    case 'Backspace': backspace(); break;
    case 'Escape': allClear(); break;
    case '%': percent(); break;
  }
});
</script>
</body>
</html>"""

st.components.v1.html(calc_html, height=460, scrolling=False)