import { useEffect } from 'react'
import '@/styles/record-dialogs.css'

/**
 * Put one inside a dialog or drawer of the Clients, Companies and Jobs screens. It does nothing you can see:
 * styles/record-dialogs.css finds it (with :has) to turn that dialog into a bottom sheet on phones, and it keeps
 * the sheet above the on-screen keyboard by publishing how much of the window the keyboard covers.
 */
export function DialogMark() {
  useEffect(() => {
    const viewport = window.visualViewport
    if (!viewport) return
    const root = document.documentElement
    const update = () => {
      const covered = Math.round(window.innerHeight - viewport.height - viewport.offsetTop)
      root.style.setProperty('--rd-keyboard', covered > 80 ? `${covered}px` : '0px')
    }
    update()
    viewport.addEventListener('resize', update)
    viewport.addEventListener('scroll', update)
    return () => {
      viewport.removeEventListener('resize', update)
      viewport.removeEventListener('scroll', update)
      root.style.removeProperty('--rd-keyboard')
    }
  }, [])
  return <span className="rd-mark" hidden />
}
