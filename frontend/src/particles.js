import * as THREE from 'three'

export function createParticleScene(canvas, lyricsZoneEl) {
  let W = window.innerWidth
  let H = window.innerHeight

  // The palette follows the album cover (see theme.js); particles get their colour from
  // vertex colours, so the sprite itself stays neutral to avoid tinting the hue.
  let palHue = 0.09
  let palSat = 0.8

  const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true })
  renderer.setSize(W, H)
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2))

  const scene = new THREE.Scene()
  const camera = new THREE.PerspectiveCamera(60, W / H, 1, 3000)
  camera.position.z = 900

  // The blurred cover / background motes want a soft sprite, but ink on a light page needs a
  // hard core: a soft blob only darkens its pixel by a few levels, which reads as nothing.
  function makeSpriteTex(innerAlpha, shoulder = 0.45) {
    const c = document.createElement('canvas')
    c.width = 32
    c.height = 32
    const ctx = c.getContext('2d')
    const g = ctx.createRadialGradient(16, 16, 0, 16, 16, 16)
    g.addColorStop(0, 'rgba(255,255,255,1)')
    g.addColorStop(0.14, `rgba(255,255,255,${innerAlpha})`)
    g.addColorStop(shoulder, 'rgba(255,255,255,0.2)')
    g.addColorStop(1, 'rgba(255,255,255,0)')
    ctx.fillStyle = g
    ctx.fillRect(0, 0, 32, 32)
    return new THREE.CanvasTexture(c)
  }

  function makeInkSpriteTex() {
    const c = document.createElement('canvas')
    c.width = 32
    c.height = 32
    const ctx = c.getContext('2d')
    // Nearly hard-edged: a soft spray lets the background bleed through every ink pixel,
    // which shows up as blurry type. Keep just enough feather to avoid aliased dots.
    const g = ctx.createRadialGradient(16, 16, 0, 16, 16, 16)
    g.addColorStop(0, 'rgba(255,255,255,1)')
    g.addColorStop(0.86, 'rgba(255,255,255,1)')
    g.addColorStop(0.94, 'rgba(255,255,255,0.45)')
    g.addColorStop(1, 'rgba(255,255,255,0)')
    ctx.fillStyle = g
    ctx.fillRect(0, 0, 32, 32)
    return new THREE.CanvasTexture(c)
  }

  function frustum() {
    const vhh = camera.position.z * Math.tan(THREE.MathUtils.degToRad(30))
    const vhw = vhh * (W / H)
    return { vhh, vhw }
  }

  let { vhh: vhh0, vhw: vhw0 } = frustum()

  const BG_N = 280
  const bgGeo = new THREE.BufferGeometry()
  const bgPos = new Float32Array(BG_N * 3)
  const bgVel = new Float32Array(BG_N * 3)
  const bgCol = new Float32Array(BG_N * 3)
  const bgPhi = new Float32Array(BG_N)
  const bgSpd = new Float32Array(BG_N)

  for (let i = 0; i < BG_N; i++) {
    const i3 = i * 3
    bgPos[i3] = (Math.random() - 0.5) * vhw0 * 2.5
    bgPos[i3 + 1] = (Math.random() - 0.5) * vhh0 * 2.5
    bgPos[i3 + 2] = (Math.random() - 0.5) * 600
    bgVel[i3] = bgVel[i3 + 1] = bgVel[i3 + 2] = 0
    bgPhi[i] = Math.random() * Math.PI * 2
    bgSpd[i] = Math.random() * 0.3 + 0.1
  }

  const tmpColor = new THREE.Color()

  function applyBgColors() {
    for (let i = 0; i < BG_N; i++) {
      const i3 = i * 3
      const jitter = ((i * 2654435761) % 1000) / 1000
      // The page is light, so the background motes are pale tints of the cover colour
      // rather than the luminous specks an additive pass on black would give.
      tmpColor.setHSL(
        (palHue + (jitter - 0.5) * 0.07 + 1) % 1,
        palSat * 0.5,
        0.6 + jitter * 0.16,
      )
      bgCol[i3] = tmpColor.r
      bgCol[i3 + 1] = tmpColor.g
      bgCol[i3 + 2] = tmpColor.b
    }
    bgGeo.attributes.color.needsUpdate = true
  }

  bgGeo.setAttribute('position', new THREE.BufferAttribute(bgPos, 3))
  bgGeo.setAttribute('color', new THREE.BufferAttribute(bgCol, 3))
  applyBgColors()

  const bgMat = new THREE.PointsMaterial({
    size: 3.5,
    map: makeSpriteTex(0.8),
    blending: THREE.NormalBlending,
    depthWrite: true,
    depthTest: true,
    vertexColors: true,
    transparent: true,
    opacity: 0.5,
  })
  scene.add(new THREE.Points(bgGeo, bgMat))

  const TXT_MAX = 120000
  const txtGeo = new THREE.BufferGeometry()
  const txtPos = new Float32Array(TXT_MAX * 3)
  const txtGoal = new Float32Array(TXT_MAX * 3)
  const txtVel = new Float32Array(TXT_MAX * 3)
  const txtCol = new Float32Array(TXT_MAX * 3)
  const txtPhi = new Float32Array(TXT_MAX)
  const txtSpd = new Float32Array(TXT_MAX)
  // 1 = the line being sung, dimmer for the lines above and below it
  const txtTone = new Float32Array(TXT_MAX)
  let txtCount = 0
  let txtCurrent = ''

  txtGeo.setAttribute('position', new THREE.BufferAttribute(txtPos, 3))
  txtGeo.setAttribute('color', new THREE.BufferAttribute(txtCol, 3))
  txtGeo.setDrawRange(0, 0)

  const txtMat = new THREE.PointsMaterial({
    // the dots have to overlap their neighbours, otherwise glyph strokes read as dotted
    size: 6,
    map: makeInkSpriteTex(),
    // Normal blending: on a light page additive light has nothing to add, and normal
    // blending also stops overlapping particles from blowing out the glyph cores.
    blending: THREE.NormalBlending,
    depthWrite: false,
    depthTest: false,
    vertexColors: true,
    transparent: true,
    opacity: 0.95,
  })
  scene.add(new THREE.Points(txtGeo, txtMat))

  function screenToWorld(sx, sy) {
    const ndcX = (sx / W) * 2 - 1
    const ndcY = -(sy / H) * 2 + 1
    return { x: ndcX * vhw0, y: ndcY * vhh0 }
  }

  const offCvs = document.createElement('canvas')
  const offCtx = offCvs.getContext('2d')
  const OFF_BASE = 1600
  const FONT_FAMILY = '"Microsoft YaHei","PingFang SC","Segoe UI",sans-serif'

  function sampleText(txt, screenRect, fontSizeScale, out, budget) {
    const ratio = screenRect.width / Math.max(screenRect.height, 1)
    let ow = OFF_BASE
    let oh = Math.round(ow / ratio)
    if (oh < 100) {
      oh = 100
      ow = Math.round(oh * ratio)
    }
    if (offCvs.width !== ow) offCvs.width = ow
    if (offCvs.height !== oh) offCvs.height = oh
    offCtx.clearRect(0, 0, ow, oh)
    let fs = Math.round(oh * fontSizeScale)
    offCtx.font = `bold ${fs}px ${FONT_FAMILY}`
    // The size only follows the raster height, so a long line runs past both edges of the
    // raster and gets chopped off. Shrink it until the measured width fits.
    const room = ow * 0.96
    const wide = offCtx.measureText(txt).width
    if (wide > room) {
      fs = Math.max(12, Math.floor((fs * room) / wide))
      offCtx.font = `bold ${fs}px ${FONT_FAMILY}`
    }
    offCtx.fillStyle = '#fff'
    offCtx.textAlign = 'center'
    offCtx.textBaseline = 'middle'
    offCtx.fillText(txt, ow / 2, oh / 2)

    const data = offCtx.getImageData(0, 0, ow, oh).data
    // A single lyric line on a tall window rasters to 150k+ points, far more than the
    // particle buffers can hold, so thin the sampling grid out to fit this section's
    // budget. Collecting straight into `out` also avoids `out.push(...pts)`, which
    // overflows the argument stack at those sizes.
    let ink = 0
    for (let i = 3; i < data.length; i += 4) {
      if (data[i] > 20) ink++
    }
    const step = Math.max(1, Math.ceil(Math.sqrt(ink / Math.max(budget, 1))))
    for (let y = 0; y < oh; y += step) {
      for (let x = 0; x < ow; x += step) {
        if (data[(y * ow + x) * 4 + 3] > 20) {
          const sx = screenRect.left + (x / ow) * screenRect.width
          const sy = screenRect.top + (y / oh) * screenRect.height
          out.push(screenToWorld(sx, sy))
        }
      }
    }
  }

  let transTimer = 0
  let transActive = false
  const TRANS_DUR = 0.65

  // Colours are derived from the index instead of a random table so that a palette change
  // can re-tint particles that are already on screen without them jumping around. The line
  // being sung is dark ink, the lines around it a light grey so they stay subordinate.
  function paint(i, tone, target) {
    const jitter = ((i * 2654435761) % 1000) / 1000
    const lifted = tone >= 1
    target.setHSL(
      (palHue + (jitter - 0.5) * 0.025 + 1) % 1,
      lifted ? palSat * 0.9 : palSat * 0.45,
      lifted ? 0.20 + jitter * 0.10 : 0.44 + jitter * 0.12,
    )
    const i3 = i * 3
    txtCol[i3] = target.r
    txtCol[i3 + 1] = target.g
    txtCol[i3 + 2] = target.b
  }

  function setAllText(prev, curr, next) {
    const zoneRect = lyricsZoneEl.getBoundingClientRect()
    const zw = zoneRect.width
    const zh = zoneRect.height
    const prevRect = { left: zoneRect.left, top: zoneRect.top, width: zw, height: zh * 0.25 }
    const currRect = { left: zoneRect.left, top: zoneRect.top + zh * 0.25, width: zw, height: zh * 0.45 }
    const nextRect = { left: zoneRect.left, top: zoneRect.top + zh * 0.70, width: zw, height: zh * 0.25 }

    const allPts = []
    if (prev) sampleText(prev, prevRect, 0.35, allPts, TXT_MAX * 0.25)
    const currStart = allPts.length
    if (curr) sampleText(curr, currRect, 0.65, allPts, TXT_MAX * 0.5)
    const currEnd = allPts.length
    if (next) sampleText(next, nextRect, 0.35, allPts, TXT_MAX * 0.25)

    const n = Math.min(allPts.length, TXT_MAX)
    transTimer = TRANS_DUR
    transActive = true

    for (let i = 0; i < n; i++) {
      const i3 = i * 3
      const gx = allPts[i].x
      const gy = allPts[i].y
      txtGoal[i3] = gx
      txtGoal[i3 + 1] = gy
      txtGoal[i3 + 2] = 0
      txtTone[i] = i >= currStart && i < currEnd ? 1 : 0.5
      paint(i, txtTone[i], tmpColor)
      if (i < txtCount) continue
      txtPos[i3] = gx + (Math.random() - 0.5) * 400
      txtPos[i3 + 1] = gy - 250 + Math.random() * -200
      txtPos[i3 + 2] = (Math.random() - 0.5) * 30
      txtVel[i3] = txtVel[i3 + 1] = txtVel[i3 + 2] = 0
      txtPhi[i] = Math.random() * Math.PI * 2
      txtSpd[i] = Math.random() * 0.4 + 0.2
    }

    txtCount = n
    txtGeo.setDrawRange(0, n)
    txtGeo.attributes.position.needsUpdate = true
    txtGeo.attributes.color.needsUpdate = true
  }

  function setTextParticles(txt, prev = '', next = '') {
    if (txt === txtCurrent && prev + '|' + next === setTextParticles._ctx) return
    txtCurrent = txt
    setTextParticles._ctx = prev + '|' + next
    setAllText(prev, txt, next)
  }
  setTextParticles._ctx = ''

  const clock = new THREE.Clock()
  const mx = new THREE.Vector2(9999, 9999)
  let running = true

  function onMove(e) {
    mx.x = e.clientX
    mx.y = e.clientY
  }
  function onLeave() {
    mx.x = mx.y = 9999
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseleave', onLeave)

  function animate() {
    if (!running) return
    requestAnimationFrame(animate)
    const dt = Math.min(clock.getDelta(), 0.1)

    for (let i = 0; i < BG_N; i++) {
      const i3 = i * 3
      bgVel[i3] += -bgPos[i3] * 0.00004
      bgVel[i3 + 1] += -bgPos[i3 + 1] * 0.00004
      bgVel[i3 + 2] += -bgPos[i3 + 2] * 0.00004
      const dx = bgPos[i3] - (mx.x / W - 0.5) * vhw0 * 2
      const dy = bgPos[i3 + 1] - (0.5 - mx.y / H) * vhh0 * 2
      const d = Math.hypot(dx, dy)
      if (d < 200 && d > 0) {
        bgVel[i3] += (dx / d) * 0.8
        bgVel[i3 + 1] += (dy / d) * 0.8
      }
      bgPhi[i] += bgSpd[i] * dt
      bgVel[i3] += Math.cos(bgPhi[i]) * 0.06
      bgVel[i3 + 1] += Math.sin(bgPhi[i] * 0.7) * 0.06
      bgVel[i3 + 2] += Math.cos(bgPhi[i] * 1.1) * 0.04
      bgVel[i3] *= 0.985
      bgVel[i3 + 1] *= 0.985
      bgVel[i3 + 2] *= 0.985
      bgPos[i3] += bgVel[i3] * dt * 15
      bgPos[i3 + 1] += bgVel[i3 + 1] * dt * 15
      bgPos[i3 + 2] += bgVel[i3 + 2] * dt * 15
    }
    bgGeo.attributes.position.needsUpdate = true

    if (transActive) {
      transTimer -= dt
      if (transTimer <= 0) {
        transTimer = 0
        transActive = false
      }
    }
    const tFrac = transActive ? Math.max(0, transTimer / TRANS_DUR) : 1

    for (let i = 0; i < txtCount; i++) {
      const i3 = i * 3
      const gx = txtGoal[i3]
      const gy = txtGoal[i3 + 1]
      let effectiveGoalY = gy
      if (transActive) {
        const dy = gy - txtPos[i3 + 1]
        if (Math.abs(dy) > 50) {
          effectiveGoalY = gy + tFrac * 350
          txtVel[i3 + 1] += 2 * (1 - tFrac)
        }
      }
      txtVel[i3] += (gx - txtPos[i3]) * 0.08
      txtVel[i3 + 1] += (effectiveGoalY - txtPos[i3 + 1]) * 0.08
      txtPhi[i] += txtSpd[i] * dt
      // small enough to keep the glyphs alive without making the outlines shimmer
      txtVel[i3] += Math.cos(txtPhi[i]) * 0.05
      txtVel[i3 + 1] += Math.sin(txtPhi[i] * 1.3) * 0.05
      txtVel[i3] *= 0.9
      txtVel[i3 + 1] *= 0.9
      txtPos[i3] += txtVel[i3] * dt * 35
      txtPos[i3 + 1] += txtVel[i3 + 1] * dt * 35
    }
    txtGeo.attributes.position.needsUpdate = true
    renderer.render(scene, camera)
  }
  animate()

  function onResize() {
    W = window.innerWidth
    H = window.innerHeight
    renderer.setSize(W, H)
    camera.aspect = W / H
    camera.updateProjectionMatrix()
    const f = frustum()
    vhh0 = f.vhh
    vhw0 = f.vhw
  }
  window.addEventListener('resize', onResize)

  function retintText() {
    for (let i = 0; i < txtCount; i++) paint(i, txtTone[i], tmpColor)
    txtGeo.attributes.color.needsUpdate = true
  }

  return {
    setTextParticles,
    setPalette(voice) {
      palHue = voice.hue
      palSat = voice.sat
      applyBgColors()
      retintText()
    },
    dispose() {
      running = false
      window.removeEventListener('resize', onResize)
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseleave', onLeave)
      renderer.dispose()
      bgGeo.dispose()
      txtGeo.dispose()
      bgMat.dispose()
      txtMat.dispose()
    },
  }
}
