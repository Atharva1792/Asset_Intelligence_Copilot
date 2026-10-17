import React, { useState, useEffect, useRef } from 'react';
import {
  Send,
  Database,
  Cpu,
  User,
  MapPin,
  Sparkles,
  HelpCircle,
  RefreshCw,
  Sliders,
  CheckCircle2,
  AlertTriangle,
  ChevronRight,
  ShieldCheck
} from 'lucide-react';
import GenerativeVisual from './components/GenerativeVisual';
import EvidenceModal from './components/EvidenceModal';
import FeedbackModal from './components/FeedbackModal';

function extractVisual(text, existingVisual) {
  if (existingVisual) return existingVisual;
  if (!text || typeof text !== 'string') return null;

  const match = text.match(/<json:visual>\\s*([\\s\\S]*?)\\s*<\\/json:visual>/i);
  if (!match) return null;

  try {
    return JSON.parse(match[1]);
  } catch (error) {
    console.error('Failed to parse visual JSON:', error);
    return null;
  }
}

export default function App() {
  const [users, setUsers] = useState([]);
  const [areas, setAreas] = useState([]);
  const [selectedUserId, setSelectedUserId] = useState('U-101');
  const [selectedAreaId, setSelectedAreaId] = useState('AREA-01');
  const [selectedModel, setSelectedModel] = useState('amazon.nova-lite-v1:0');

  const [messages, setMessages] = useState([
    {
      role: 'assistant',
      content:
        "Welcome to the **Asset Intelligence Copilot**. I am grounded in your TimescaleDB operational dataset, weekly drone thermal inspections, and maintenance work orders.\n\nAsk any question about well status, downtime calculations, or root cause evidence in your operational area.",
      citations: [],
      visual: null,
    },
  ]);
  const [inputPrompt, setInputPrompt] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [healthStats, setHealthStats] = useState(null);
  const [activeOverrides, setActiveOverrides] = useState([]);

  // Modals
  const [activeCitation, setActiveCitation] = useState(null);
  const [feedbackTarget, setFeedbackTarget] = useState(null);

  const messagesEndRef = useRef(null);

  useEffect(() => {
    // 1. Fetch Health Stats
    fetch('/api/health')
      .then((r) => r.json())
      .then((d) => setHealthStats(d.stats))
      .catch(() => {});

    // 2. Fetch Users
    fetch('/api/users')
      .then((r) => r.json())
      .then((d) => {
        if (d.users) setUsers(d.users);
      })
      .catch(() => {});

    // 3. Fetch Areas
    fetch('/api/areas')
      .then((r) => r.json())
      .then((d) => {
        if (d.areas) setAreas(d.areas);
      })
      .catch(() => {});

    // 4. Fetch Active Overrides
    fetch('/api/feedback/active')
      .then((r) => r.json())
      .then((d) => {
        if (d.overrides) setActiveOverrides(d.overrides);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isLoading]);

  const handleSend = async (queryText) => {
    const promptToSend = queryText || inputPrompt;
    if (!promptToSend.trim() || isLoading) return;

    const userMessage = { role: 'user', content: promptToSend };
    setMessages((prev) => [...prev, userMessage]);
    if (!queryText) setInputPrompt('');
    setIsLoading(true);

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt: promptToSend,
          user_id: selectedUserId,
          session_id: `session-${selectedUserId}`,
          model_name: selectedModel,
        }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `Server error: ${res.statusText}`);
      }

      const data = await res.json();
      const visual = extractVisual(data.text, data.visual);
      const cleanText = (data.text || '')
        .replace(/<json:visual>[\\s\\S]*?<\\/json:visual>/i, '')
        .trim();

      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: cleanText,
          visual,
          citations: data.citations || [],
          model: data.model_used,
        },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: `⚠️ **Operational Notice:** ${err.message}`,
        },
      ]);
    } finally {
      setIsLoading(false);
    }
  };

  // Helper to format citation tags as interactive buttons
  const renderMessageContent = (text, onSelectCitation) => {
    if (!text) return null;
    const citationRegex = /\[(INS-[A-Za-z0-9\-]+|MNT-[A-Za-z0-9\-]+|SOP-[A-Za-z0-9\-]+|Report:[^\]]+)\]/g;
    const parts = [];
    let lastIndex = 0;
    let match;

    while ((match = citationRegex.exec(text)) !== null) {
      if (match.index > lastIndex) {
        parts.push(text.substring(lastIndex, match.index));
      }
      const cit = match[1];
      parts.push(
        <button
          key={match.index}
          type="button"
          className="citation-pill"
          onClick={() => onSelectCitation(cit)}
          title="Click to view authoritative source evidence"
        >
          {cit}
        </button>
      );
      lastIndex = match.index + match[0].length;
    }
    if (lastIndex < text.length) {
      parts.push(text.substring(lastIndex));
    }

    return (
      <div style={{ whiteSpace: 'pre-wrap' }}>
        {parts.map((p, i) => (typeof p === 'string' ? p : <React.Fragment key={i}>{p}</React.Fragment>))}
      </div>
    );
  };

  return (
    <div className="app-layout">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="sidebar-header">
          <div className="brand-icon">🛢️</div>
          <div className="brand-text">
            <h1>Asset Intelligence</h1>
            <span>Gemma 4 • TimescaleDB</span>
          </div>
        </div>

        <div className="sidebar-content">
          {/* Live DB Stats */}
          <div>
            <div className="section-label">
              <Database size={13} />
              <span>Operational Telemetry</span>
            </div>
            <div style={{ background: 'var(--bg-card)', padding: '12px', borderRadius: '10px', border: '1px solid var(--border-subtle)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '6px' }}>
                <span style={{ color: 'var(--text-muted)' }}>Wells Indexed:</span>
                <span style={{ color: 'var(--accent-amber)', fontWeight: 600 }}>{healthStats?.wells_indexed ?? 200}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '6px' }}>
                <span style={{ color: 'var(--text-muted)' }}>Inspections:</span>
                <span style={{ color: 'var(--accent-cyan)', fontWeight: 600 }}>{healthStats?.inspections_indexed?.toLocaleString() ?? '39,500+'}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px' }}>
                <span style={{ color: 'var(--text-muted)' }}>pgvector Chunks:</span>
                <span style={{ color: 'var(--accent-emerald)', fontWeight: 600 }}>{healthStats?.document_chunks_indexed?.toLocaleString() ?? '14,000+'}</span>
              </div>
            </div>
          </div>

          {/* Active Overrides */}
          {activeOverrides.length > 0 && (
            <div>
              <div className="section-label">
                <ShieldCheck size={13} color="#10b981" />
                <span>Human Overrides ({activeOverrides.length})</span>
              </div>
              <div style={{ fontSize: '11.5px', color: 'var(--text-secondary)' }}>
                {activeOverrides.slice(0, 3).map((o, idx) => (
                  <div key={idx} style={{ marginBottom: '6px', background: 'rgba(16, 185, 129, 0.08)', padding: '6px 8px', borderRadius: '6px' }}>
                    <span style={{ color: 'var(--accent-emerald)', fontWeight: 600 }}>{o.target_id}:</span> {o.rationale}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </aside>

      {/* Main Chat Stream */}
      <main className="chat-container">
        {/* Top Header */}
        <header className="chat-header">
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <span className="status-badge live">
              <span className="status-dot"></span>
              Live Operational Ground Truth
            </span>
            <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
              Deterministic Thermal Delta T Downtime Calculation
            </span>
          </div>

          <button
            type="button"
            onClick={() => setFeedbackTarget('FLEET-GENERAL')}
            style={{
              background: 'rgba(245, 158, 11, 0.1)',
              border: '1px solid rgba(245, 158, 11, 0.3)',
              color: 'var(--accent-amber)',
              borderRadius: '8px',
              padding: '6px 12px',
              fontSize: '12px',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}
          >
            <AlertTriangle size={13} />
            Submit Operator Override
          </button>
        </header>

        {/* Message Thread */}
        <div className="chat-messages">
          {messages.map((m, idx) => (
            <div key={idx} className={`message-wrapper ${m.role}`}>
              <div className={`avatar ${m.role === 'user' ? 'user' : 'bot'}`}>
                {m.role === 'user' ? <User size={18} /> : '🛢️'}
              </div>
              <div className="message-body">
                <div className="message-bubble">
                  {renderMessageContent(m.content, setActiveCitation)}
                  {m.visual && (
                    <GenerativeVisual
                      visual={m.visual}
                      onSelectCitation={setActiveCitation}
                    />
                  )}
                </div>

                {/* Citation pill summary row if present */}
                {m.citations && m.citations.length > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '4px' }}>
                    <span style={{ fontSize: '11px', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '4px' }}>
                      <CheckCircle2 size={12} color="#10b981" /> Authoritative Sources:
                    </span>
                    {m.citations.map((c, i) => (
                      <button
                        key={i}
                        type="button"
                        className="citation-pill"
                        onClick={() => setActiveCitation(c)}
                      >
                        {c}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}

          {isLoading && (
            <div className="message-wrapper assistant">
              <div className="avatar bot">🛢️</div>
              <div className="message-body">
                <div className="message-bubble" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <div className="typing-dots">
                    <span></span>
                    <span></span>
                    <span></span>
                  </div>
                  <span style={{ fontSize: '13px', color: 'var(--accent-amber)' }}>
                    Thinking...
                  </span>
                </div>
              </div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Bottom Input Area */}
        <footer className="chat-input-container">
          <form
            className="chat-input-box"
            onSubmit={(e) => {
              e.preventDefault();
              handleSend();
            }}
          >
            <input
              type="text"
              className="chat-input"
              placeholder="Ask an operational question (e.g., 'Which wells in my area were down the longest this summer, and why?')..."
              value={inputPrompt}
              onChange={(e) => setInputPrompt(e.target.value)}
              disabled={isLoading}
            />
            <button type="submit" className="send-btn" disabled={isLoading || !inputPrompt.trim()}>
              <Send size={15} />
              <span>Ask Copilot</span>
            </button>
          </form>
        </footer>
      </main>

      {/* Citation Drill-down Modal */}
      {activeCitation && (
        <EvidenceModal
          citation={activeCitation}
          onClose={() => setActiveCitation(null)}
          onOpenFeedback={(cit) => setFeedbackTarget(cit)}
        />
      )}

      {/* Operator Override Modal */}
      {feedbackTarget && (
        <FeedbackModal
          targetId={feedbackTarget}
          currentUserId={selectedUserId}
          onClose={() => setFeedbackTarget(null)}
          onSuccess={(res) => {
            fetch('/api/feedback/active')
              .then((r) => r.json())
              .then((d) => setActiveOverrides(d.overrides || []));
          }}
        />
      )}
    </div>
  );
}
