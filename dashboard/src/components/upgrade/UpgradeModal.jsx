import { useEffect, useRef, useState } from 'react';
import { X, ArrowRight, ArrowLeft, PartyPopper, Landmark } from 'lucide-react';
import BankDetails from './BankDetails';
import UpgradeForm from './UpgradeForm';

export default function UpgradeModal({ plan, me, onClose }) {
  const [step, setStep] = useState(1);
  const [submitted, setSubmitted] = useState(false);
  const dialogRef = useRef(null);

  useEffect(() => {
    dialogRef.current?.focus();
    const onKeyDown = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 300, display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'rgba(15,23,42,0.5)', padding: '20px', animation: 'rowFadeIn 0.15s ease',
      }}
      onClick={onClose}
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="upgrade-modal-title"
        onClick={e => e.stopPropagation()}
        style={{
          background: 'var(--bg-surface)', borderRadius: '18px', width: '100%', maxWidth: '480px',
          maxHeight: '88vh', overflowY: 'auto', boxShadow: '0 24px 60px rgba(15,23,42,0.25)',
          outline: 'none',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '20px 24px', borderBottom: '1px solid var(--border-subtle)' }}>
          <div>
            <div id="upgrade-modal-title" style={{ fontSize: '15px', fontWeight: '700', color: 'var(--text-primary)' }}>
              {submitted ? 'Request submitted' : `Upgrade to ${plan.label}`}
            </div>
            {!submitted && (
              <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginTop: '2px' }}>
                Step {step} of 2: {step === 1 ? 'Payment instructions' : 'Your details'}
              </div>
            )}
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-tertiary)', padding: '4px', display: 'flex' }}
          >
            <X size={18} />
          </button>
        </div>

        <div style={{ padding: '24px' }}>
          {submitted ? (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center', gap: '10px', padding: '12px 0' }}>
              <div style={{ width: '48px', height: '48px', borderRadius: '50%', background: 'var(--success-bg)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <PartyPopper size={22} color="var(--success)" />
              </div>
              <div style={{ fontSize: '14px', fontWeight: '600', color: 'var(--text-primary)' }}>We've got your request</div>
              <p style={{ fontSize: '13px', color: 'var(--text-secondary)', margin: 0, lineHeight: '1.6' }}>
                We'll verify your payment and activate {plan.label} on your account shortly. If you forgot to add a
                transaction reference, just reply to any of our emails with it.
              </p>
              <button
                onClick={onClose}
                style={{ marginTop: '10px', padding: '10px 24px', borderRadius: '9px', border: 'none', background: 'var(--cyan-500)', color: 'white', fontSize: '13.5px', fontWeight: '700', cursor: 'pointer' }}
              >
                Done
              </button>
            </div>
          ) : step === 1 ? (
            <>
              <div style={{ display: 'flex', gap: '10px', alignItems: 'flex-start', padding: '12px 14px', background: 'var(--bg-subtle)', borderRadius: '10px', marginBottom: '18px' }}>
                <Landmark size={16} color="var(--cyan-600)" style={{ flexShrink: 0, marginTop: '1px' }} />
                <p style={{ margin: 0, fontSize: '12.5px', color: 'var(--text-secondary)', lineHeight: '1.6' }}>
                  Payments are currently verified manually. Send payment to the account below, then continue to submit
                  your transaction reference. We'll activate your account once it's confirmed.
                </p>
              </div>
              <BankDetails />
              <button
                onClick={() => setStep(2)}
                style={{
                  marginTop: '20px', width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px',
                  padding: '11px', borderRadius: '9px', border: 'none', background: 'var(--cyan-500)',
                  color: 'white', fontSize: '13.5px', fontWeight: '700', cursor: 'pointer',
                }}
              >
                Continue <ArrowRight size={15} />
              </button>
            </>
          ) : (
            <>
              <button
                onClick={() => setStep(1)}
                style={{ display: 'flex', alignItems: 'center', gap: '4px', background: 'none', border: 'none', color: 'var(--text-tertiary)', fontSize: '12px', fontWeight: '600', cursor: 'pointer', padding: 0, marginBottom: '16px' }}
              >
                <ArrowLeft size={13} /> Back to payment details
              </button>
              <UpgradeForm
                plan={plan}
                initialName={me?.company_name}
                initialEmail={me?.email}
                onSuccess={() => setSubmitted(true)}
              />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
