export function parseLRC(text) {
  const lines = []
  const re1 = /\[\s*(\d+):(\d+)[\.:](\d+)\s*\]\s*(.+?)\s*$/
  const re2 = /\[\s*(\d+):(\d+)\s*\]\s*(.+?)\s*$/
  const meta = /^\[(?:ti|ar|al|by|offset|length|re|ve|id|la):/i

  for (const raw of text.split(/\r?\n/)) {
    if (!raw.trim() || meta.test(raw)) continue
    let m = raw.match(re1)
    if (m) {
      const min = parseInt(m[1], 10)
      const sec = parseInt(m[2], 10)
      const ms = m[3].length === 2 ? parseInt(m[3], 10) * 10 : parseInt(m[3].substring(0, 3), 10)
      const t = min * 60 + sec + ms / 1000
      const txt = m[4].trim()
      if (txt) lines.push({ time: t, text: txt })
      continue
    }
    m = raw.match(re2)
    if (m) {
      const t = parseInt(m[1], 10) * 60 + parseInt(m[2], 10)
      const txt = m[3].trim()
      if (txt) lines.push({ time: t, text: txt })
    }
  }
  lines.sort((a, b) => a.time - b.time)
  return lines
}

export function fmtTime(s) {
  if (s <= 0 || !isFinite(s)) return '0:00'
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return m + ':' + String(sec).padStart(2, '0')
}

export function findSyncedIndex(lyrics, pos) {
  let lo = 0
  let hi = lyrics.length - 1
  let idx = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (lyrics[mid].time <= pos) {
      idx = mid
      lo = mid + 1
    } else {
      hi = mid - 1
    }
  }
  return idx
}
