import { useState } from 'react';
import { Copy, Check } from 'lucide-react';

const FIELDS = [
  ['Bank Name', 'United Bank Limited (UBL)'],
  ['Account Title', 'Bushra Zohaib'],
  ['Account Number', '1052342011278'],
  ['IBAN', 'PK09UNIL0109000342011278'],
  ['SWIFT Code (BIC)', 'UNILPKKA'],
  ['Country', 'Pakistan'],
  ['City', 'Karachi, Sindh'],
  ['Postcode', '75800'],
  ['Street/Region', 'Block A, North Nazimabad'],
];

function CopyRow({ label, value }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard API unavailable (e.g. insecure context) — silently no-op,
      // the value is still selectable/readable in the row itself.
    }
  };

  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '12px', padding: '9px 0', borderBottom: '1px solid var(--border-subtle)' }}>
      <span style={{ color: 'var(--text-secondary)', fontSize: '12.5px' }}>{label}</span>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
        <span style={{ color: 'var(--text-primary)', fontWeight: '500', fontFamily: 'DM Mono, monospace', fontSize: '12.5px', textAlign: 'right', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {value}
        </span>
        <button
          onClick={copy}
          aria-label={`Copy ${label}`}
          title={copied ? 'Copied' : `Copy ${label}`}
          style={{
            background: 'none', border: 'none', cursor: 'pointer', flexShrink: 0,
            display: 'flex', padding: '4px', borderRadius: '5px',
            color: copied ? 'var(--success)' : 'var(--text-tertiary)',
          }}
        >
          {copied ? <Check size={13} /> : <Copy size={13} />}
        </button>
      </div>
    </div>
  );
}

export default function BankDetails() {
  return (
    <div>
      {FIELDS.map(([label, value]) => (
        <CopyRow key={label} label={label} value={value} />
      ))}
    </div>
  );
}
