<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { createParticleScene } from './particles'
import { createSocket } from './socket'
import { findSyncedIndex, fmtTime, parseLRC } from './lyrics'
import { applyPalette, paletteFromImage } from './theme'

const canvasRef = ref(null)
const lyricsZoneRef = ref(null)
const progressTrackRef = ref(null)

const title = ref('等待音乐')
const artist = ref('打开音乐播放器')
const thumbnail = ref('')
// 兜底模式下 SMTC 没有封面，歌词接口会顺带带回一张，两者都当作封面用
const lyricsCover = ref('')
const coverArt = computed(() => thumbnail.value || lyricsCover.value)
const ambientStyle = computed(() => (coverArt.value ? { backgroundImage: `url("${coverArt.value}")` } : {}))
const playStatus = ref('stopped')
const lastPos = ref(0)
const lastDur = ref(0)
// fallback mode: no SMTC session, so the timeline is estimated and a seek is performed by
// asking the backend to click the player's own progress bar
const fallbackMode = ref(false)

const lyrics = ref([])
const lyricType = ref('none')
const activeIdx = ref(-1)
// -1 = not dragging; >= 0 = horizontal ratio the user is dragging to
const dragRatio = ref(-1)
let hasSyncedLyrics = false
let lastLyricSrc = ''

const playing = computed(() => playStatus.value === 'playing')
const progressPct = computed(() => {
  if (dragRatio.value >= 0) return dragRatio.value * 100
  if (lastDur.value <= 0) return 0
  return Math.min(100, (lastPos.value / lastDur.value) * 100)
})
const shownPos = computed(() => (dragRatio.value >= 0 ? dragRatio.value * lastDur.value : lastPos.value))
const prevLyric = computed(() => {
  const i = activeIdx.value
  return i > 0 ? lyrics.value[i - 1].text : ''
})
const activeLyric = computed(() => {
  const i = activeIdx.value
  return i >= 0 && lyrics.value[i] ? lyrics.value[i].text : ''
})
const nextLyric = computed(() => {
  const i = activeIdx.value
  return i >= 0 && i < lyrics.value.length - 1 ? lyrics.value[i + 1].text : ''
})

let particles = null
let socket = null

function resetLyrics() {
  hasSyncedLyrics = false
  lastLyricSrc = ''
  lyrics.value = []
  lyricType.value = 'none'
  activeIdx.value = -1
  particles?.setTextParticles('', '', '')
}

function applyMusic(info) {
  resetLyrics()
  thumbnail.value = info.thumbnail || ''
  lyricsCover.value = ''
  title.value = info.title || '未知歌曲'
  artist.value = info.artist || ''
}

function applyLyrics(src, type) {
  if (hasSyncedLyrics && type !== 'synced') return
  const key = type + ':' + src
  if (key === lastLyricSrc) return
  lastLyricSrc = key

  if (type === 'synced' && src) {
    const parsed = parseLRC(src)
    if (parsed.length > 0) {
      lyrics.value = parsed
      lyricType.value = 'synced'
      hasSyncedLyrics = true
      activeIdx.value = -1
    } else {
      lyricType.value = 'none'
    }
  } else if (src && !hasSyncedLyrics) {
    lyrics.value = src.split('\n').map((l) => l.trim()).filter(Boolean).map((t) => ({ time: -1, text: t }))
    lyricType.value = 'plain'
    activeIdx.value = -1
  } else if (!src && !hasSyncedLyrics) {
    lyrics.value = []
    lyricType.value = 'none'
  }
  if (lyricType.value === 'synced' && lyrics.value.length) {
    syncLyrics(lastPos.value)
  }
}

function syncLyrics(pos) {
  if (lyricType.value !== 'synced' || !lyrics.value.length) return
  // findSyncedIndex returns -1 before the first timestamp, which would blank the whole
  // lyrics area during an intro or whenever the position is still unknown; keep the first
  // line up instead.
  const idx = Math.max(0, findSyncedIndex(lyrics.value, pos))
  if (idx === activeIdx.value) return
  activeIdx.value = idx
}

function syncLyricsPlain(pos, dur) {
  let idx = Math.floor((pos / dur) * lyrics.value.length)
  if (idx >= lyrics.value.length) idx = lyrics.value.length - 1
  if (idx < 0) idx = 0
  if (idx === activeIdx.value) return
  activeIdx.value = idx
}

function applyPosition(pos, dur) {
  lastPos.value = pos
  lastDur.value = dur
  if (!lyrics.value.length) return
  if (lyricType.value === 'synced') syncLyrics(pos)
  else if (lyricType.value === 'plain' && dur > 0) syncLyricsPlain(pos, dur)
}

watch([activeLyric, prevLyric, nextLyric], ([curr, prev, next]) => {
  particles?.setTextParticles(curr, prev, next)
})

// Re-derive the accent colour whenever the artwork changes. The token guards against a
// slow decode overwriting the palette of a track that is already playing.
let paletteToken = 0
watch(coverArt, async (src) => {
  const token = ++paletteToken
  const palette = await paletteFromImage(src)
  if (token !== paletteToken) return
  applyPalette(palette)
  particles?.setPalette(palette.voice)
})

function send(msg) {
  socket?.send(msg)
}

function closeWindow() {
  send('close')
  try {
    if (window.pywebview?.api?.close) {
      window.pywebview.api.close()
      return
    }
  } catch { /* noop */ }
  window.close()
}

function trackRatio(e) {
  const rect = progressTrackRef.value.getBoundingClientRect()
  if (!rect.width) return 0
  return Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width))
}

function onTrackDown(e) {
  if (lastDur.value <= 0) return
  e.preventDefault()
  dragRatio.value = trackRatio(e)
  progressTrackRef.value.setPointerCapture(e.pointerId)
}

function onTrackMove(e) {
  if (dragRatio.value < 0) return
  dragRatio.value = trackRatio(e)
}

function onTrackUp(e) {
  if (dragRatio.value < 0) return
  const ratio = trackRatio(e)
  dragRatio.value = -1
  try {
    progressTrackRef.value.releasePointerCapture(e.pointerId)
  } catch { /* noop */ }
  send('seek:' + (ratio * lastDur.value).toFixed(2))
}

function onKey(e) {
  if (e.code === 'Space') {
    e.preventDefault()
    send('playpause')
  } else if (e.code === 'ArrowLeft') send('prev')
  else if (e.code === 'ArrowRight') send('next')
  else if (e.code === 'Escape') closeWindow()
}

onMounted(async () => {
  await nextTick()
  particles = createParticleScene(canvasRef.value, lyricsZoneRef.value)
  socket = createSocket((event) => {
    if (typeof event.fallback === 'boolean') fallbackMode.value = event.fallback
    switch (event.type) {
      case 'music':
        applyMusic(event)
        break
      case 'playback':
        playStatus.value = event.status
        break
      case 'position':
        applyPosition(event.pos, event.dur)
        break
      case 'lyrics':
        applyLyrics(event.source, event.kind)
        break
      case 'cover':
        // 播放器自己给了封面（SMTC 缩略图）时优先用它
        if (!thumbnail.value) lyricsCover.value = event.url || ''
        break
      case 'lyrics-status':
        if (event.status === 'no-lyrics') artist.value = '无可用歌词'
        break
      case 'status':
        artist.value = event.message || artist.value
        break
    }
  })
  document.addEventListener('keydown', onKey)
})

onUnmounted(() => {
  document.removeEventListener('keydown', onKey)
  socket?.close()
  particles?.dispose()
})
</script>

<template>
  <div class="ambient" aria-hidden="true">
    <div class="ambient-cover" :style="ambientStyle"></div>
    <div class="ambient-blob blob-a"></div>
    <div class="ambient-blob blob-b"></div>
    <div class="ambient-vignette"></div>
  </div>

  <canvas ref="canvasRef" class="fx-canvas"></canvas>

  <header class="topbar">
    <span class="brand">
      <i class="brand-dot" :class="{ live: playing }"></i>
      <span class="brand-text">小雨音乐控制器</span>
    </span>
    <button class="topbar-close" title="关闭" @click="closeWindow">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M6.4 6.4 17.6 17.6M17.6 6.4 6.4 17.6" />
      </svg>
    </button>
  </header>

  <main class="main-body">
    <section class="album-section">
      <div class="album-frame">
        <div class="album-glow"></div>
        <div class="album-ring" :class="{ playing }"></div>
        <div v-if="!coverArt" class="album-placeholder">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1">
            <circle cx="12" cy="12" r="10" />
            <circle cx="12" cy="12" r="6" />
            <circle cx="12" cy="12" r="2" />
          </svg>
        </div>
        <img v-else class="album-art" :class="{ playing }" :src="coverArt" alt="" />
      </div>
      <div class="song-meta">
        <div class="song-title">{{ title }}</div>
        <div class="song-artist">{{ artist }}</div>
      </div>
    </section>

    <div ref="lyricsZoneRef" class="lyrics-zone"></div>
  </main>

  <footer class="bottombar glass">
    <div class="ctrls">
      <button class="ctrl-btn" title="上一首" @click="send('prev')">
        <svg class="ico" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M19 5.6v12.8L9.8 12 19 5.6Z" />
          <rect x="5" y="5.4" width="2.4" height="13.2" rx="1.2" />
        </svg>
      </button>
      <button class="ctrl-btn play-btn" :class="{ playing }" title="播放/暂停" @click="send('playpause')">
        <svg v-if="playing" class="ico" viewBox="0 0 24 24" aria-hidden="true">
          <rect x="7" y="5" width="3.6" height="14" rx="1.5" />
          <rect x="13.4" y="5" width="3.6" height="14" rx="1.5" />
        </svg>
        <svg v-else class="ico" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M8.2 5.2v13.6L19 12 8.2 5.2Z" />
        </svg>
      </button>
      <button class="ctrl-btn" title="下一首" @click="send('next')">
        <svg class="ico" viewBox="0 0 24 24" aria-hidden="true">
          <path d="M5 5.6v12.8L14.2 12 5 5.6Z" />
          <rect x="16.6" y="5.4" width="2.4" height="13.2" rx="1.2" />
        </svg>
      </button>
    </div>
    <div class="progress-area">
      <div
        ref="progressTrackRef"
        class="progress-track"
        :class="{ dragging: dragRatio >= 0 }"
        @pointerdown="onTrackDown"
        @pointermove="onTrackMove"
        @pointerup="onTrackUp"
        @pointercancel="onTrackUp"
      >
        <div class="progress-fill" :style="{ width: progressPct + '%' }"></div>
      </div>
      <div class="progress-times">
        <span>{{ fmtTime(shownPos) }}</span>
        <span
          v-if="fallbackMode"
          class="progress-hint"
          title="兜底模式：网易云未向系统注册播放会话，进度与歌词位置按估算时间推进（总长按 4 分钟估算）。拖动进度条会通过点击播放器自己的进度条来真实跳转。"
          >估算进度</span
        >
        <span>{{ fmtTime(lastDur) }}</span>
      </div>
    </div>
  </footer>
</template>

<style scoped>
/* ---------- ambient background ---------- */
.ambient {
  position: fixed;
  inset: 0;
  z-index: 0;
  overflow: hidden;
  pointer-events: none;
}
.ambient-cover {
  position: absolute;
  inset: -12%;
  background-position: center;
  background-size: cover;
  /* a day theme washes the artwork across the page instead of darkening it */
  filter: blur(88px) saturate(150%) brightness(1.22);
  opacity: .32;
  transform: scale(1.08);
  transition: opacity .8s var(--ease);
}
.ambient-blob {
  position: absolute;
  border-radius: 50%;
  filter: blur(70px);
  opacity: .55;
  transition: background .8s var(--ease);
}
.blob-a {
  width: 46vw;
  height: 46vw;
  left: -10vw;
  top: -14vh;
  background: radial-gradient(circle, var(--accent-soft), transparent 68%);
  animation: driftA 26s ease-in-out infinite;
}
.blob-b {
  width: 40vw;
  height: 40vw;
  right: -8vw;
  bottom: -14vh;
  background: radial-gradient(circle, var(--accent-soft), transparent 68%);
  animation: driftB 32s ease-in-out infinite;
}
@keyframes driftA {
  0%, 100% { transform: translate3d(0, 0, 0) scale(1); }
  50% { transform: translate3d(6vw, 5vh, 0) scale(1.15); }
}
@keyframes driftB {
  0%, 100% { transform: translate3d(0, 0, 0) scale(1.1); }
  50% { transform: translate3d(-5vw, -4vh, 0) scale(1); }
}
.ambient-vignette {
  position: absolute;
  inset: 0;
  /* fades the artwork wash into the page so the edges stay calm and the centre stays airy */
  background:
    radial-gradient(125% 95% at 50% 44%, rgba(255,255,255,.3) 0%, rgba(252,253,255,.88) 100%),
    linear-gradient(180deg, rgba(252,253,255,.7), transparent 24%, transparent 68%, rgba(252,253,255,.72));
}

.fx-canvas {
  position: fixed;
  inset: 0;
  z-index: 1;
  pointer-events: none;
}

/* ---------- glass surface ---------- */
.glass {
  background: linear-gradient(180deg, var(--glass-hi), var(--glass-bg));
  backdrop-filter: blur(22px) saturate(150%);
  -webkit-backdrop-filter: blur(22px) saturate(150%);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-soft), inset 0 1px 0 #fff;
  position: relative;
  overflow: hidden;
}
.glass::before {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  border-radius: inherit;
  background: linear-gradient(135deg, transparent 30%, rgba(255,255,255,0.3) 45%, rgba(255,255,255,0.62) 50%, rgba(255,255,255,0.3) 55%, transparent 70%);
  background-size: 200% 200%;
  animation: glassSweep 9s ease-in-out infinite;
}
@keyframes glassSweep {
  0% { background-position: 0% 0%; }
  50% { background-position: 100% 100%; }
  100% { background-position: 0% 0%; }
}

/* ---------- top bar ---------- */
.topbar {
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  z-index: 10;
  position: relative;
  flex-shrink: 0;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  cursor: move;
  -webkit-app-region: drag;
  app-region: drag;
}
.brand-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-faint);
  transition: background .3s var(--ease);
  flex-shrink: 0;
}
.brand-dot.live {
  background: var(--accent);
  animation: dotPulse 2.6s ease-out infinite;
}
@keyframes dotPulse {
  0% { box-shadow: 0 0 0 0 var(--accent-glow); }
  70% { box-shadow: 0 0 0 9px transparent; }
  100% { box-shadow: 0 0 0 0 transparent; }
}
.brand-text {
  /* 中文用不上全大写拉丁那套宽字距：4.5px 在 10px 字号下接近半个字宽，会散成一排。
     字号提到 11px 是因为汉字在 10px 下笔画会糊。 */
  font-size: 11px;
  letter-spacing: 2px;
  color: var(--text-faint);
}
.topbar-close {
  width: 30px;
  height: 30px;
  border-radius: 9px;
  border: none;
  background: transparent;
  color: var(--text-faint);
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: background .18s var(--ease), color .18s var(--ease);
  flex-shrink: 0;
  -webkit-app-region: no-drag;
}
.topbar-close svg {
  width: 14px;
  height: 14px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.7;
  stroke-linecap: round;
}
.topbar-close:hover {
  background: rgba(214,58,58,.12);
  color: #cf3b3b;
}

/* ---------- album + meta ---------- */
.main-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  /* the control bar floats over the page: keep the lyric bands clear of it */
  padding: 12px 48px 104px;
  z-index: 2;
  position: relative;
  min-height: 0;
  height: calc(100vh - 44px);
}

.album-section {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 18px;
  margin-bottom: 26px;
  flex-shrink: 0;
}
.album-frame { position: relative; width: 200px; height: 200px; }
.album-glow {
  position: absolute;
  inset: -22%;
  border-radius: 50%;
  background: radial-gradient(circle, var(--accent-glow), transparent 62%);
  filter: blur(22px);
  opacity: .7;
  z-index: 0;
}
.album-ring {
  position: absolute;
  inset: -9px;
  border-radius: 50%;
  border: 2px solid transparent;
  background: conic-gradient(from 0deg, var(--accent), var(--accent-2), var(--accent)) border-box;
  -webkit-mask: linear-gradient(#fff 0 0) padding-box, linear-gradient(#fff 0 0);
  mask: linear-gradient(#fff 0 0) padding-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  opacity: 0;
  transition: opacity .4s var(--ease);
  animation: ringSpin 5s linear infinite;
  animation-play-state: paused;
  z-index: 2;
}
.album-ring.playing { opacity: .85; animation-play-state: running; }
@keyframes ringSpin { to { transform: rotate(360deg); } }

.album-art {
  width: 200px;
  height: 200px;
  border-radius: 50%;
  object-fit: cover;
  box-shadow: var(--shadow-lift);
  position: relative;
  z-index: 1;
}
.album-art.playing { animation: discSpin 20s linear infinite; }
@keyframes discSpin { to { transform: rotate(360deg); } }

.album-placeholder {
  width: 200px;
  height: 200px;
  border-radius: 50%;
  background: radial-gradient(circle at 50% 50%, #fff, var(--accent-deep));
  border: 1px solid var(--glass-border);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: var(--shadow-soft);
  position: relative;
  z-index: 1;
}
.album-placeholder svg { width: 64px; height: 64px; opacity: .5; color: var(--accent); }

.song-meta { text-align: center; max-width: 72vw; }
.song-title {
  font-size: 21px;
  font-weight: 600;
  line-height: 1.3;
  color: var(--text);
  letter-spacing: .3px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.song-artist {
  font-size: 12.5px;
  color: var(--text-dim);
  letter-spacing: 1.2px;
  margin-top: 6px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.lyrics-zone {
  flex: 1;
  width: 100%;
  max-width: 1280px;
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 0;
}

/* ---------- control bar ---------- */
.bottombar {
  position: absolute;
  bottom: 22px;
  left: 50%;
  transform: translateX(-50%);
  width: calc(100% - 96px);
  max-width: 1180px;
  height: 64px;
  display: flex;
  align-items: center;
  padding: 0 26px;
  gap: 20px;
  z-index: 10;
  flex-shrink: 0;
  border-radius: var(--radius-lg);
}
.bottombar::after {
  content: '';
  position: absolute;
  inset: 0 0 auto;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--accent-soft) 35%, var(--accent-soft) 65%, transparent);
  pointer-events: none;
}
.bottombar > * { position: relative; z-index: 1; }

.ctrls { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
.ctrl-btn {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  border: 1px solid var(--glass-border);
  background: rgba(255,255,255,.5);
  color: var(--text-dim);
  cursor: pointer;
  display: grid;
  place-items: center;
  transition: transform .18s var(--ease), color .18s var(--ease),
              background .18s var(--ease), border-color .18s var(--ease),
              box-shadow .18s var(--ease);
}
.ctrl-btn .ico { width: 17px; height: 17px; fill: currentColor; }
.ctrl-btn:hover {
  color: var(--text);
  background: #fff;
  border-color: rgba(20,26,38,.14);
  box-shadow: 0 6px 16px rgba(21,28,42,.12);
  transform: translateY(-1px);
}
.ctrl-btn:active { transform: translateY(0) scale(.95); }

.play-btn {
  width: 54px;
  height: 54px;
  border: none;
  color: #fff;
  background: linear-gradient(140deg, var(--accent), var(--accent-2));
  box-shadow: 0 6px 20px var(--accent-glow);
}
.play-btn .ico { width: 20px; height: 20px; }
.play-btn:hover {
  color: #fff;
  transform: translateY(-2px);
  box-shadow: 0 10px 28px var(--accent-glow);
}
.play-btn.playing { box-shadow: 0 6px 22px var(--accent-glow), 0 0 0 4px var(--accent-soft); }

.progress-area { flex: 1; display: flex; flex-direction: column; gap: 7px; min-width: 0; }
.progress-track {
  width: 100%;
  height: 6px;
  background: rgba(23,26,33,.10);
  box-shadow: inset 0 1px 2px rgba(21,28,42,.10);
  border-radius: 3px;
  cursor: pointer;
  position: relative;
  touch-action: none;
  transition: background .18s var(--ease);
}
/* a 6px bar is hard to hit: widen the pointer target without changing the look */
.progress-track::before {
  content: '';
  position: absolute;
  inset: -10px 0;
}
.progress-track:hover { background: rgba(23,26,33,.16); }
.progress-fill {
  height: 100%;
  width: 0%;
  border-radius: 3px;
  background: linear-gradient(90deg, var(--accent), var(--accent-2));
  transition: width .15s linear;
  box-shadow: 0 0 12px var(--accent-glow);
  position: relative;
}
.progress-track.dragging .progress-fill { transition: none; }
.progress-fill::after {
  content: '';
  position: absolute;
  right: -9px;
  top: 50%;
  transform: translateY(-50%);
  width: 13px;
  height: 13px;
  border-radius: 50%;
  background: #fff;
  /* a plain white knob would vanish on a light track: ring it with the accent colour */
  border: 2px solid var(--accent);
  box-shadow: 0 2px 8px rgba(21,28,42,.28);
  opacity: 0;
  transition: opacity .18s var(--ease);
}
.progress-track:hover .progress-fill::after,
.progress-track.dragging .progress-fill::after { opacity: 1; }
.progress-times {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 11px;
  color: var(--text-faint);
  font-variant-numeric: tabular-nums;
  letter-spacing: .6px;
}
.progress-hint {
  color: var(--text-faint);
  opacity: .75;
  cursor: help;
}
</style>
