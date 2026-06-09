import { useNavigate } from 'react-router-dom';
import Badge from './Badge';

function formatDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export default function TicketRow({ ticket, index }) {
  const navigate = useNavigate();
  const id = ticket.id || ticket.ticket_id;

  return (
    <tr
      onClick={() => navigate(`/tickets/${id}`)}
      style={{
        background: index % 2 === 1 ? 'var(--bg-secondary)' : 'var(--bg-primary)',
        cursor: 'pointer',
        transition: 'background 0.1s',
      }}
      onMouseEnter={e => e.currentTarget.style.background = 'var(--accent-light)'}
      onMouseLeave={e => e.currentTarget.style.background = index % 2 === 1 ? 'var(--bg-secondary)' : 'var(--bg-primary)'}
    >
      <td style={{ padding: '10px 16px', fontFamily: 'DM Mono, monospace', fontSize: '12px', color: 'var(--text-muted)' }}>
        #{String(id).slice(0, 8)}
      </td>
      <td style={{ padding: '10px 16px', color: 'var(--text-primary)' }}>
        {ticket.customer_email || ticket.customer_name || '—'}
      </td>
      <td style={{ padding: '10px 16px', color: 'var(--text-primary)', maxWidth: '260px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {ticket.subject || ticket.message?.slice(0, 60) || '—'}
      </td>
      <td style={{ padding: '10px 16px', color: 'var(--text-secondary)', fontSize: '13px' }}>
        {ticket.channel || ticket.source_channel || 'email'}
      </td>
      <td style={{ padding: '10px 16px' }}>
        <Badge status={ticket.status} />
      </td>
      <td style={{ padding: '10px 16px', color: 'var(--text-muted)', fontSize: '13px', fontFamily: 'DM Mono, monospace' }}>
        {formatDate(ticket.created_at)}
      </td>
      <td style={{ padding: '10px 16px', color: 'var(--text-muted)', fontSize: '13px', fontFamily: 'DM Mono, monospace' }}>
        {formatDate(ticket.updated_at)}
      </td>
    </tr>
  );
}
