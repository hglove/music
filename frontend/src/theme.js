// The widget takes its accent colour from the album cover so the UI always matches the
// track that is playing. Covers arrive from the backend as data URIs (see lyrics.py),
// which keeps the canvas readable: a cross-origin <img> would taint it.

const FALLBACK_HUE = 38
const SAT_MIN = 0.5
const SAT_MAX = 0.92
const HUE_BINS = 12

function rgbToHsl(r, g, b) {
  r /= 255
  g /= 255
  b /= 255
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const l = (max + min) / 2
  if (max === min) return [0, 0, l]
  const d = max - min
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
  let h
  if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6
  else if (max === g) h = ((b - r) / d + 2) / 6
  else h = ((r - g) / d + 4) / 6
  return [h, s, l]
}

function dominantHue(img) {
  const size = 40
  const cvs = document.createElement('canvas')
  cvs.width = size
  cvs.height = size
  const ctx = cvs.getContext('2d', { willReadFrequently: true })
  if (!ctx) return null
  ctx.drawImage(img, 0, 0, size, size)
  const { data } = ctx.getImageData(0, 0, size, size)

  const weight = new Array(HUE_BINS).fill(0)
  const hueSum = new Array(HUE_BINS).fill(0)
  const satSum = new Array(HUE_BINS).fill(0)
  let total = 0

  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] < 200) continue
    const [h, s, l] = rgbToHsl(data[i], data[i + 1], data[i + 2])
    // Near-black, blown-out and washed-out pixels say nothing about the artwork's colour.
    if (s < 0.18 || l < 0.12 || l > 0.9) continue
    const w = s * (1 - Math.abs(l - 0.5) * 1.2)
    if (w <= 0) continue
    const bin = Math.min(HUE_BINS - 1, Math.floor(h * HUE_BINS))
    weight[bin] += w
    hueSum[bin] += w * h
    satSum[bin] += w * s
    total += w
  }
  if (total < 2) return null

  let best = 0
  for (let b = 1; b < HUE_BINS; b++) {
    if (weight[b] > weight[best]) best = b
  }
  if (weight[best] / total < 0.16) return null
  return { hue: (hueSum[best] / weight[best]) * 360, sat: satSum[best] / weight[best] }
}

export function paletteFor(theme) {
  const hue = theme ? theme.hue : FALLBACK_HUE
  const sat = theme ? Math.min(SAT_MAX, Math.max(SAT_MIN, theme.sat)) : 0.82
  const h = Math.round(hue)
  const s = Math.round(sat * 100)
  return {
    // Three.js takes hue in 0..1. The day theme paints the lyrics as dark ink, so the
    // voice only carries hue and saturation — the lightness is chosen in particles.js.
    voice: { hue: ((h % 360) + 360) % 360 / 360, sat },
    accent: `hsl(${h} ${s}% 42%)`,
    accent2: `hsl(${(h + 26) % 360} ${s}% 34%)`,
    glow: `hsl(${h} ${s}% 45% / 0.32)`,
    soft: `hsl(${h} ${s}% 45% / 0.13)`,
    deep: `hsl(${h} ${Math.round(sat * 55)}% 90%)`,
  }
}

export function applyPalette(palette) {
  const style = document.documentElement.style
  style.setProperty('--accent', palette.accent)
  style.setProperty('--accent-2', palette.accent2)
  style.setProperty('--accent-glow', palette.glow)
  style.setProperty('--accent-soft', palette.soft)
  style.setProperty('--accent-deep', palette.deep)
}

export function paletteFromImage(src) {
  if (!src) return Promise.resolve(paletteFor(null))
  return new Promise((resolve) => {
    const img = new Image()
    img.onload = () => {
      let theme = null
      try {
        theme = dominantHue(img)
      } catch {
        theme = null
      }
      resolve(paletteFor(theme))
    }
    img.onerror = () => resolve(paletteFor(null))
    img.src = src
  })
}
