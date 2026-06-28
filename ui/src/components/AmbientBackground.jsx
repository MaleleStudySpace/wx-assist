import { useEffect, useRef } from 'react'

/**
 * Full-page ambient wave background.
 * Slow sine-wave ripples drifting like wind over wheat fields
 * or gentle sound waves. Calm, not flashy.
 *
 * Light mode: warm grey waves on white
 * Dark mode: cool blue-silver waves on dark
 */
export function AmbientWaveBackground() {
  const canvasRef = useRef(null)
  const animRef = useRef(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    let width = 0, height = 0

    function isDark() {
      return document.documentElement.classList.contains('dark')
    }

    function resize() {
      width = window.innerWidth
      height = window.innerHeight
      canvas.width = width * devicePixelRatio
      canvas.height = height * devicePixelRatio
      canvas.style.width = `${width}px`
      canvas.style.height = `${height}px`
      ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0)
    }
    resize()
    window.addEventListener('resize', resize)

    // Wave layers: each has different speed, amplitude, wavelength
    const waves = [
      { y: 0.20, amplitude: 25, wavelength: 350, speed: 0.08, phase: 0, lineW: 1.2, bandH: 40 },
      { y: 0.38, amplitude: 18, wavelength: 280, speed: -0.06, phase: 1.8, lineW: 0.9, bandH: 30 },
      { y: 0.55, amplitude: 30, wavelength: 420, speed: 0.05, phase: 3.2, lineW: 1.4, bandH: 50 },
      { y: 0.72, amplitude: 15, wavelength: 240, speed: -0.07, phase: 4.8, lineW: 0.8, bandH: 25 },
      { y: 0.88, amplitude: 20, wavelength: 380, speed: 0.06, phase: 2.0, lineW: 1.1, bandH: 35 },
    ]

    function draw(time) {
      ctx.clearRect(0, 0, width, height)
      const dark = isDark()
      const t = time / 1000

      for (const w of waves) {
        const baseY = height * w.y
        const phase = w.phase + t * w.speed

        // Compute wave path
        const points = []
        for (let x = 0; x <= width; x += 2) {
          const y = baseY + Math.sin((x / w.wavelength) * Math.PI * 2 + phase) * w.amplitude
          points.push({ x, y })
        }

        // Draw a soft filled band between the wave and a line below it
        const bandBottom = w.bandH
        const fillAlpha = dark ? 0.012 : 0.015
        const strokeAlpha = dark ? 0.06 : 0.08

        // Fill band
        ctx.beginPath()
        for (let i = 0; i < points.length; i++) {
          const { x, y } = points[i]
          if (i === 0) ctx.moveTo(x, y)
          else ctx.lineTo(x, y)
        }
        // Close the band: go to bottom-right, then bottom-left
        ctx.lineTo(width, baseY + bandBottom)
        ctx.lineTo(0, baseY + bandBottom)
        ctx.closePath()

        ctx.fillStyle = dark
          ? `rgba(130,150,185,${fillAlpha})`
          : `rgba(190,185,178,${fillAlpha})`
        ctx.fill()

        // Stroke the wave line on top
        ctx.beginPath()
        for (let i = 0; i < points.length; i++) {
          const { x, y } = points[i]
          if (i === 0) ctx.moveTo(x, y)
          else ctx.lineTo(x, y)
        }
        ctx.strokeStyle = dark
          ? `rgba(130,150,185,${strokeAlpha})`
          : `rgba(190,185,178,${strokeAlpha})`
        ctx.lineWidth = w.lineW
        ctx.stroke()
      }

      animRef.current = requestAnimationFrame(draw)
    }

    animRef.current = requestAnimationFrame(draw)

    return () => {
      cancelAnimationFrame(animRef.current)
      window.removeEventListener('resize', resize)
    }
  }, [])

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 pointer-events-none select-none"
      style={{ zIndex: 0 }}
    />
  )
}
