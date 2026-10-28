import { useEffect, useRef } from 'react'
import type { MouseEvent as ReactMouseEvent, ThHTMLAttributes } from 'react'

export interface ResizableHeaderCellProps extends ThHTMLAttributes<HTMLTableCellElement> {
  width?: number
  minWidth?: number
  onResize?: (width: number) => void
}

export default function ResizableHeaderCell({
  width,
  minWidth = 80,
  onResize,
  children,
  className,
  ...restProps
}: ResizableHeaderCellProps) {
  const cleanupRef = useRef<(() => void) | null>(null)

  useEffect(() => () => cleanupRef.current?.(), [])

  const startResize = (event: ReactMouseEvent<HTMLSpanElement>) => {
    if (!width || !onResize) return
    event.preventDefault()
    event.stopPropagation()

    const startX = event.clientX
    const startWidth = width
    document.body.classList.add('is-resizing-column')

    const handleMove = (moveEvent: MouseEvent) => {
      onResize(Math.max(minWidth, startWidth + moveEvent.clientX - startX))
    }
    const stopResize = () => {
      document.body.classList.remove('is-resizing-column')
      window.removeEventListener('mousemove', handleMove)
      window.removeEventListener('mouseup', stopResize)
      cleanupRef.current = null
    }

    cleanupRef.current = stopResize
    window.addEventListener('mousemove', handleMove)
    window.addEventListener('mouseup', stopResize)
  }

  return (
    <th {...restProps} className={`${className ?? ''} resizable-header-cell`}>
      {children}
      {width && onResize ? (
        <span
          className="column-resize-handle"
          role="separator"
          aria-label="拖动调整列宽"
          aria-orientation="vertical"
          onMouseDown={startResize}
          onClick={(event) => event.stopPropagation()}
        />
      ) : null}
    </th>
  )
}
