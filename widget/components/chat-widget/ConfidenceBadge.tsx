'use client'

type Props = {
  confidence?: number
}

export function ConfidenceBadge({ confidence }: Props) {
  if (confidence === undefined) return null

  const isHigh = confidence >= 80
  const isMed  = confidence >= 50
  const color  = isHigh ? '#10B981' : isMed ? '#F59E0B' : '#EF4444'
  const label  = confidence < 50 ? 'Human recommended' : `${confidence}% confident`

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '4px',
        marginTop: '5px',
        paddingLeft: '2px',
      }}
    >
      <div
        style={{
          width: '6px',
          height: '6px',
          borderRadius: '50%',
          background: color,
          flexShrink: 0,
        }}
      />
      <span
        style={{
          fontSize: '10px',
          letterSpacing: '0.04em',
          color,
          opacity: 0.65,
          fontWeight: 400,
          lineHeight: 1,
        }}
      >
        {label}
      </span>
    </div>
  )
}
