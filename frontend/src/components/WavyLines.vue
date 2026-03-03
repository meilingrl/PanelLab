<script setup>
/**
 * 可随鼠标交互的波浪线条背景，参考：
 * https://github.com/JIEJOE-WEB-Tutorial/016-wavy-lines
 * 竖向 SVG 线条网格，鼠标移动时产生排斥/波纹，带弹性与阻尼。
 */
import { ref, onMounted, onUnmounted } from 'vue'

const containerRef = ref(null)
const svgRef = ref(null)

let bounding = { width: 0, height: 0 }
let mouse = {
  x: 0,
  y: 0,
  lx: 0,
  ly: 0,
  sx: 0,
  sy: 0,
  v: 0,
  vs: 0,
  a: 0,
}
let lines = []
let paths = []
let rafId = null

function setSize() {
  if (!containerRef.value) return
  bounding = containerRef.value.getBoundingClientRect()
  if (svgRef.value) {
    svgRef.value.setAttribute('width', bounding.width)
    svgRef.value.setAttribute('height', bounding.height)
  }
}

function setLines() {
  if (!containerRef.value || !svgRef.value) return
  const { width, height } = bounding
  if (width <= 0 || height <= 0) return

  const xGap = 10
  const yGap = 32
  const oWidth = width + 200
  const oHeight = height + 30
  const totalLines = Math.ceil(oWidth / xGap)
  const totalPoints = Math.ceil(oHeight / yGap)
  const xStart = (width - xGap * totalLines) / 2
  const yStart = (height - yGap * totalPoints) / 2

  lines = []
  paths.forEach((p) => p.remove())
  paths = []

  const ns = 'http://www.w3.org/2000/svg'
  for (let i = 0; i <= totalLines; i++) {
    const points = []
    for (let j = 0; j <= totalPoints; j++) {
      points.push({
        x: xStart + xGap * i,
        y: yStart + yGap * j,
        cursor: { x: 0, y: 0, vx: 0, vy: 0 },
      })
    }
    lines.push(points)
    const path = document.createElementNS(ns, 'path')
    path.setAttribute('fill', 'none')
    path.setAttribute('stroke-width', '1')
    path.setAttribute('class', 'wavy-line-path')
    svgRef.value.appendChild(path)
    paths.push(path)
  }
}

function updateMousePosition(clientX, clientY) {
  if (!containerRef.value) return
  const rect = containerRef.value.getBoundingClientRect()
  mouse.x = clientX - rect.left
  mouse.y = clientY - rect.top
}

function movePoints() {
  lines.forEach((points) => {
    points.forEach((p) => {
      const dx = p.x - mouse.sx
      const dy = p.y - mouse.sy
      const d = Math.hypot(dx, dy)
      const l = Math.max(175, mouse.vs)

      if (d < l) {
        const f = 1 - d / l
        p.cursor.vx += Math.cos(mouse.a) * f * mouse.vs * 0.08
        p.cursor.vy += Math.sin(mouse.a) * f * mouse.vs * 0.08
      }

      p.cursor.vx += (0 - p.cursor.x) * 0.005
      p.cursor.vy += (0 - p.cursor.y) * 0.005
      p.cursor.vx *= 0.925
      p.cursor.vy *= 0.925
      p.cursor.x += p.cursor.vx * 2
      p.cursor.y += p.cursor.vy * 2
      p.cursor.x = Math.min(100, Math.max(-100, p.cursor.x))
      p.cursor.y = Math.min(100, Math.max(-100, p.cursor.y))
    })
  })
}

function moved(point, withCursorForce = true) {
  const coords = {
    x: point.x + (withCursorForce ? point.cursor.x : 0),
    y: point.y + (withCursorForce ? point.cursor.y : 0),
  }
  coords.x = Math.round(coords.x * 10) / 10
  coords.y = Math.round(coords.y * 10) / 10
  return coords
}

function drawLines() {
  lines.forEach((points, lIndex) => {
    let p1 = moved(points[0], false)
    let d = `M ${p1.x} ${p1.y}`
    points.forEach((p, pIndex) => {
      const isLast = pIndex === points.length - 1
      const pt = moved(p, !isLast)
      d += ` L ${pt.x} ${pt.y}`
    })
    if (paths[lIndex]) paths[lIndex].setAttribute('d', d)
  })
}

function tick() {
  mouse.sx += (mouse.x - mouse.sx) * 0.1
  mouse.sy += (mouse.y - mouse.sy) * 0.1
  const dx = mouse.x - mouse.lx
  const dy = mouse.y - mouse.ly
  const d = Math.hypot(dx, dy)
  mouse.v = d
  mouse.vs += (d - mouse.vs) * 0.1
  mouse.vs = Math.min(100, mouse.vs)
  mouse.lx = mouse.x
  mouse.ly = mouse.y
  mouse.a = Math.atan2(dy, dx)
  movePoints()
  drawLines()
  rafId = requestAnimationFrame(tick)
}

function onResize() {
  setSize()
  setLines()
}

function onMouseMove(e) {
  updateMousePosition(e.clientX, e.clientY)
}

function onTouchMove(e) {
  if (e.touches.length) {
    e.preventDefault()
    const t = e.touches[0]
    updateMousePosition(t.clientX, t.clientY)
  }
}

onMounted(() => {
  setSize()
  setLines()
  window.addEventListener('resize', onResize)
  window.addEventListener('mousemove', onMouseMove)
  containerRef.value?.addEventListener('touchmove', onTouchMove, { passive: false })
  rafId = requestAnimationFrame(tick)
})

onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  window.removeEventListener('mousemove', onMouseMove)
  containerRef.value?.removeEventListener('touchmove', onTouchMove)
  if (rafId != null) cancelAnimationFrame(rafId)
})
</script>

<template>
  <div ref="containerRef" class="wavy-lines" aria-hidden="true">
    <svg ref="svgRef" class="wavy-lines-svg" />
  </div>
</template>

<style scoped>
.wavy-lines {
  position: absolute;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
}

.wavy-lines-svg {
  display: block;
  width: 100%;
  height: 100%;
}

:deep(.wavy-line-path) {
  stroke: var(--border);
  opacity: 0.5;
  transition: opacity 0.15s;
}
</style>
