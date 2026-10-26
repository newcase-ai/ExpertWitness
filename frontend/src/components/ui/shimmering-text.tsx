import * as React from 'react'
import { cn } from '@/lib/utils'

interface ShimmeringTextProps extends React.HTMLAttributes<HTMLSpanElement> {
  text: string
  duration?: number
  wave?: boolean
  color?: string
  shimmeringColor?: string
}

function ShimmeringText({
  text,
  duration = 2,
  wave = false,
  className,
  color = 'currentColor',
  shimmeringColor = 'hsl(231 70% 85%)',
  style,
  ...props
}: ShimmeringTextProps) {
  const letters = text.split('')
  const delayPerLetter = 0.1 // 100ms stagger between each letter

  return (
    <>
      <style>
        {`
          @keyframes shimmer-color {
            0%, 40%, 100% { color: var(--shimmer-base); }
            20% { color: var(--shimmer-highlight); }
          }
          @keyframes shimmer-wave {
            0%, 40%, 100% { transform: translateY(0); }
            20% { transform: translateY(-2px); }
          }
        `}
      </style>
      <span
        className={cn('inline-flex', className)}
        style={
          {
            '--shimmer-base': color,
            '--shimmer-highlight': shimmeringColor,
            ...style,
          } as React.CSSProperties
        }
        {...props}
      >
        {letters.map((char, i) => (
          <span
            key={i}
            className="inline-block"
            style={{
              animation: `shimmer-color ${duration}s ease-in-out infinite${wave ? `, shimmer-wave ${duration}s ease-in-out infinite` : ''}`,
              animationDelay: `${i * delayPerLetter}s`,
              color: 'var(--shimmer-base)',
            }}
          >
            {char === ' ' ? '\u00A0' : char}
          </span>
        ))}
      </span>
    </>
  )
}

export { ShimmeringText, type ShimmeringTextProps }
