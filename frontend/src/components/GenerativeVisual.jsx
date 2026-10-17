import React from 'react';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
  CartesianGrid
} from 'recharts';
import { AlertCircle, CheckCircle2, MapPin, Table as TableIcon, BarChart2 } from 'lucide-react';

export default function GenerativeVisual({ visual, onSelectCitation }) {
  if (!visual) return null;

  const type =
    visual.type ||
    visual.component ||
    (visual.data
      ? 'BarChartVisual'
      : visual.status && visual.metrics
        ? 'StatusCard'
        : 'unknown');

  // 1. Bar Chart Visual (e.g. Downtime Ranking)
  if (type === 'BarChartVisual' || visual.component === 'BarChartVisual' || visual.data) {
    const data = visual.data || [];
    const title = visual.title || 'Downtime Comparison (Days)';
    const xKey = visual.xKey || 'name';
    const yKey = visual.yKey || 'downtime_days';

    return (
      <div className="visual-card">
        <div className="visual-title">
          <BarChart2 size={16} />
          <span>{title}</span>
        </div>
        <div style={{ width: '100%', height: 260 }}>
          <ResponsiveContainer>
            <BarChart data={data} margin={{ top: 10, right: 20, left: -10, bottom: 25 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
              <XAxis
                dataKey={xKey}
                stroke="#64748b"
                tick={{ fill: '#94a3b8', fontSize: 11 }}
                angle={-25}
                textAnchor="end"
              />
              <YAxis
                stroke="#64748b"
                tick={{ fill: '#94a3b8', fontSize: 11 }}
                unit="d"
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#0f172a',
                  borderColor: 'rgba(245, 158, 11, 0.4)',
                  borderRadius: '8px',
                  color: '#f8fafc',
                  fontSize: '12px'
                }}
                formatter={(val) => [`${val} Days Downtime`, 'Downtime']}
              />
              <Bar dataKey={yKey} radius={[4, 4, 0, 0]}>
                {data.map((entry, index) => {
                  const days = entry[yKey] || 0;
                  const color = days > 50 ? '#ef4444' : days > 25 ? '#f59e0b' : '#06b6d4';
                  return <Cell key={`cell-${index}`} fill={color} />;
                })}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    );
  }

  // 2. Status Card Visual
  if (type === 'StatusCard' || visual.component === 'StatusCard') {
    const status = visual.status || 'UNKNOWN';
    const isGood = status.toUpperCase().includes('PRODUCING');

    return (
      <div className="visual-card">
        <div className="visual-title">
          {isGood ? <CheckCircle2 size={16} color="#10b981" /> : <AlertCircle size={16} color="#ef4444" />}
          <span>{visual.title || 'Asset Operational Status'}</span>
          <span className={`status-badge ${isGood ? 'live' : 'offline'}`} style={{ marginLeft: 'auto' }}>
            <span className="status-dot"></span>
            {status}
          </span>
        </div>
        {visual.metrics && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: '12px', marginTop: '12px' }}>
            {Object.entries(visual.metrics).map(([k, v]) => (
              <div key={k} style={{ background: 'rgba(255,255,255,0.03)', padding: '10px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.05)' }}>
                <div style={{ fontSize: '11px', color: '#64748b', textTransform: 'uppercase' }}>{k.replace(/_/g, ' ')}</div>
                <div style={{ fontSize: '15px', fontWeight: 600, color: '#f8fafc', marginTop: '4px' }}>{String(v)}</div>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  // 3. DataTable Visual
  if (type === 'DataTable' || visual.component === 'DataTable' || (visual.columns && visual.rows)) {
    const cols = visual.columns || [];
    const rows = visual.rows || [];

    return (
      <div className="visual-card">
        <div className="visual-title">
          <TableIcon size={16} />
          <span>{visual.title || 'Operational Log Records'}</span>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table className="visual-table">
            <thead>
              <tr>
                {cols.map((c) => (
                  <th key={c}>{c.replace(/_/g, ' ')}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  {cols.map((c) => (
                    <td key={c}>
                      {typeof r[c] === 'string' && (r[c].startsWith('INS-') || r[c].startsWith('MNT-')) ? (
                        <button
                          type="button"
                          className="citation-pill"
                          onClick={() => onSelectCitation && onSelectCitation(r[c])}
                        >
                          {r[c]}
                        </button>
                      ) : (
                        String(r[c] !== undefined && r[c] !== null ? r[c] : '-')
                      )}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  }

  // 4. MapPinView Visual
  if (type === 'MapPinView' || visual.component === 'MapPinView') {
    const pins = visual.pins || [];

    return (
      <div className="visual-card">
        <div className="visual-title">
          <MapPin size={16} />
          <span>{visual.title || 'Geographic Well Coordinates'}</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '10px' }}>
          {pins.map((p, idx) => (
            <div key={idx} style={{ background: 'rgba(255,255,255,0.03)', padding: '10px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.06)' }}>
              <div style={{ fontWeight: 600, fontSize: '13px', color: '#f59e0b' }}>{p.label || p.name || `Well ${idx + 1}`}</div>
              <div style={{ fontSize: '11px', color: '#94a3b8', marginTop: '4px' }}>
                Lat: {p.lat?.toFixed(4)}, Lon: {p.lon?.toFixed(4)}
              </div>
              {p.status && (
                <div style={{ fontSize: '11px', color: p.status.includes('PRODUCING') ? '#10b981' : '#f43f5e', marginTop: '2px' }}>
                  {p.status}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    );
  }

  return null;
}
