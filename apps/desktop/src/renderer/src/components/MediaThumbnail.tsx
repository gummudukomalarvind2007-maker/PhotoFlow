import { memo, useEffect, useRef, useState } from 'react'
import { Icon } from './Icon'
import { thumbnailCache } from './thumbnailCache'

interface MediaThumbnailProps {
  mediaId: number
  mediaType: 'photo' | 'video'
  alt?: string
}

export const MediaThumbnail = memo(function MediaThumbnail({ mediaId, mediaType, alt = '' }: MediaThumbnailProps) {
  const elementRef = useRef<HTMLSpanElement>(null)
  const [nearViewport, setNearViewport] = useState(false)
  const [thumbnail, setThumbnail] = useState<string | null>(null)

  useEffect(() => {
    const element = elementRef.current
    if (!element || typeof IntersectionObserver === 'undefined') {
      setNearViewport(true)
      return
    }
    const observer = new IntersectionObserver(([entry]) => {
      setNearViewport(entry.isIntersecting)
    }, { rootMargin: '500px' })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (!nearViewport) {
      setThumbnail(null)
      return
    }
    let active = true
    void thumbnailCache.load(mediaId, () => window.photoFlow.getThumbnail(mediaId)).then((value) => {
      if (active) setThumbnail(value)
    }).catch((error: unknown) => {
      if (active) {
        console.error(`Could not load thumbnail for media ${mediaId}.`, error)
        setThumbnail(null)
      }
    })
    return () => { active = false }
  }, [mediaId, nearViewport])

  return <span className="media-thumbnail" ref={elementRef}>
    {thumbnail
      ? <img src={thumbnail} alt={alt} loading="lazy" decoding="async" />
      : <Icon name={mediaType === 'photo' ? 'image' : 'video'} size={28} />}
  </span>
})
