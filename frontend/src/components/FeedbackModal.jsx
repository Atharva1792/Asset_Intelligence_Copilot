import React, { useState } from 'react';
import { X, Send, AlertTriangle, CheckCircle } from 'lucide-react';

export default function FeedbackModal({ targetId, currentUserId, onClose, onSuccess }) {
  const [targetType, setTargetType] = useState('raw_reading');
  const [rationale, setRationale] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!rationale.trim()) return;

    setSubmitting(true);
    setError(null);

    try {
      const payload = {
        user_id: currentUserId || 'U-101',
        target_type: targetType,
        target_id: targetId || 'GENERAL',
        correction_payload: {
          override_active: true,
          submitted_at: new Date().toISOString(),
          correction_scope: targetType,
        },
        rationale: rationale.trim(),
      };

      const res = await fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        throw new Error(`Failed to submit feedback: ${res.statusText}`);
      }

      const data = await res.json();
      if (onSuccess) onSuccess(data);
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{
      position: 'fixed',
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      backgroundColor: 'rgba(5, 8, 15, 0.85)',
      backdropFilter: 'blur(8px)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: 110,
      padding: '20px',
    }}>
      <div style={{
        backgroundColor: '#0f172a',
        border: '1px solid rgba(239, 68, 68, 0.4)',
        borderRadius: '16px',
        width: '100%',
        maxWidth: '520px',
        boxShadow: '0 20px 50px rgba(0, 0, 0, 0.7)',
        overflow: 'hidden',
      }}>
        {/* Header */}
        <div style={{
          padding: '16px 20px',
          borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          background: 'rgba(239, 68, 68, 0.08)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <AlertTriangle size={18} color="#ef4444" />
            <h3 style={{ fontSize: '15px', color: '#f8fafc', fontWeight: 600 }}>
              Operator Human Override & Correction
            </h3>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'transparent',
              border: 'none',
              color: '#94a3b8',
              cursor: 'pointer',
              padding: '4px',
            }}
          >
            <X size={18} />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} style={{ padding: '24px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div>
            <label style={{ fontSize: '11px', color: '#94a3b8', textTransform: 'uppercase', fontWeight: 600, display: 'block', marginBottom: '6px' }}>
              Target Evidence / Asset ID
            </label>
            <input
              type="text"
              readOnly
              value={targetId || 'FLEET-GENERAL'}
              style={{
                width: '100%',
                background: 'rgba(255, 255, 255, 0.04)',
                border: '1px solid rgba(255, 255, 255, 0.08)',
                color: '#f59e0b',
                fontFamily: 'JetBrains Mono, monospace',
                fontSize: '13px',
                padding: '10px 12px',
                borderRadius: '8px',
                outline: 'none',
              }}
            />
          </div>

          <div>
            <label style={{ fontSize: '11px', color: '#94a3b8', textTransform: 'uppercase', fontWeight: 600, display: 'block', marginBottom: '6px' }}>
              Override Category
            </label>
            <select
              value={targetType}
              onChange={(e) => setTargetType(e.target.value)}
              className="select-control"
            >
              <option value="raw_reading">Sensor Reading / Thermal Camera Calibration Drift</option>
              <option value="spill_detection">Spill Detection False Positive / False Negative</option>
              <option value="well_status">Operational Status Override (Active / Down)</option>
            </select>
          </div>

          <div>
            <label style={{ fontSize: '11px', color: '#94a3b8', textTransform: 'uppercase', fontWeight: 600, display: 'block', marginBottom: '6px' }}>
              Operator Rationale & Engineering Justification
            </label>
            <textarea
              required
              rows={4}
              placeholder="Explain the physical operational ground truth (e.g., 'Drone IR lens showed calibration offset of +12°C during this flight; well was verified producing via manual gauge')."
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
              style={{
                width: '100%',
                background: '#121a2c',
                border: '1px solid rgba(255, 255, 255, 0.1)',
                color: '#f8fafc',
                fontSize: '13.5px',
                padding: '12px',
                borderRadius: '8px',
                outline: 'none',
                resize: 'vertical',
                fontFamily: 'Inter, sans-serif',
              }}
            />
          </div>

          {error && (
            <div style={{ color: '#f87171', fontSize: '12px', background: 'rgba(239, 68, 68, 0.1)', padding: '8px 12px', borderRadius: '6px' }}>
              {error}
            </div>
          )}

          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px', marginTop: '8px' }}>
            <button
              type="button"
              onClick={onClose}
              style={{
                background: '#1e293b',
                color: '#f8fafc',
                border: '1px solid rgba(255, 255, 255, 0.1)',
                borderRadius: '8px',
                padding: '8px 16px',
                fontSize: '12.5px',
                cursor: 'pointer',
              }}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              style={{
                background: 'linear-gradient(135deg, #ef4444, #dc2626)',
                color: 'white',
                border: 'none',
                borderRadius: '8px',
                padding: '8px 18px',
                fontSize: '12.5px',
                fontWeight: 600,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
              }}
            >
              <Send size={14} />
              {submitting ? 'Applying...' : 'Apply System Override'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
