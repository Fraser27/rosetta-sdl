import { useEffect, useState } from 'react'
import { api, type DocumentSummary, type DocumentDetail } from '../api'
import { Link } from 'react-router-dom'

export default function Documents() {
  const [docs, setDocs] = useState<DocumentSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<DocumentDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [toast, setToast] = useState<{ msg: string; type: string } | null>(null)
  const [editingDesc, setEditingDesc] = useState(false)
  const [descDraft, setDescDraft] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    api.listDocuments()
      .then(setDocs)
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  const showToast = (msg: string, type = 'success') => {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 3000)
  }

  const openDetail = async (key: string) => {
    setDetailLoading(true)
    try {
      const detail = await api.getDocument(key)
      setSelected(detail)
      setDescDraft(detail.description || '')
      setEditingDesc(false)
    } catch (e: unknown) {
      showToast((e as Error).message, 'error')
    } finally {
      setDetailLoading(false)
    }
  }

  const handleDescSave = async () => {
    if (!selected) return
    setSaving(true)
    try {
      await api.updateDocumentDescription(selected.s3_key, descDraft)
      setSelected((d) => d ? { ...d, description: descDraft } : d)
      setDocs((all) => all.map((d) => d.s3_key === selected.s3_key ? { ...d, description: descDraft } : d))
      setEditingDesc(false)
      showToast('Description updated')
    } catch (e: unknown) {
      showToast((e as Error).message, 'error')
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="loading"><div className="spinner" /></div>

  return (
    <>
      <div className="page-header">
        <h2>Documents</h2>
        <p>Unstructured documents discovered from S3 Vector buckets</p>
      </div>

      {docs.length === 0 ? (
        <div className="card">
          <div className="empty-state">No documents found. Run a scan from the Admin page to discover S3 Vector buckets.</div>
        </div>
      ) : (
        <div className="card">
          <table className="data-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th>Description</th>
                <th>Related Tables</th>
                <th>Concepts</th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.s3_key} onClick={() => openDetail(d.s3_key)} style={{ cursor: 'pointer' }}>
                  <td><strong>{d.name}</strong></td>
                  <td><span className="tag tag-purple">{d.type}</span></td>
                  <td style={{ color: 'var(--text-dim)', fontSize: 13, maxWidth: 300 }}>{d.description || '-'}</td>
                  <td>
                    {d.related_tables.filter(Boolean).map((t) => (
                      <span key={t} className="tag tag-blue" style={{ marginRight: 4 }}>{t}</span>
                    ))}
                    {d.related_tables.filter(Boolean).length === 0 && '-'}
                  </td>
                  <td>
                    {d.concepts.filter(Boolean).map((c) => (
                      <span key={c} className="tag tag-green" style={{ marginRight: 4 }}>{c}</span>
                    ))}
                    {d.concepts.filter(Boolean).length === 0 && '-'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Detail panel */}
      {selected && (
        <div className="modal-overlay" onClick={() => setSelected(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 700 }}>
            {detailLoading ? (
              <div className="loading"><div className="spinner" /></div>
            ) : (
              <>
                <h3>{selected.name}</h3>

                <div className="detail-grid" style={{ marginBottom: 16 }}>
                  <div>
                    <div className="detail-field">
                      <div className="label">S3 Key</div>
                      <div className="value" style={{ fontSize: 12, wordBreak: 'break-all' }}>{selected.s3_key}</div>
                    </div>
                    <div className="detail-field">
                      <div className="label">Type</div>
                      <div className="value"><span className="tag tag-purple">{selected.type}</span></div>
                    </div>
                  </div>
                  <div>
                    <div className="detail-field">
                      <div className="label">Vector Bucket</div>
                      <div className="value">{selected.vector_bucket || '-'}</div>
                    </div>
                    <div className="detail-field">
                      <div className="label">Vector Index</div>
                      <div className="value">{selected.vector_index || '-'}</div>
                    </div>
                  </div>
                </div>

                <div className="form-group">
                  <label>Description</label>
                  {editingDesc ? (
                    <div style={{ display: 'flex', gap: 8 }}>
                      <input
                        value={descDraft}
                        onChange={(e) => setDescDraft(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') handleDescSave(); if (e.key === 'Escape') setEditingDesc(false) }}
                        autoFocus
                        style={{ flex: 1 }}
                      />
                      <button className="btn btn-primary btn-sm" onClick={handleDescSave} disabled={saving}>
                        {saving ? '...' : 'Save'}
                      </button>
                      <button className="btn btn-ghost btn-sm" onClick={() => setEditingDesc(false)}>Cancel</button>
                    </div>
                  ) : (
                    <p
                      onClick={() => setEditingDesc(true)}
                      style={{ cursor: 'pointer', borderBottom: '1px dashed var(--border)', color: 'var(--text-dim)', fontSize: 13 }}
                    >
                      {selected.description || 'Click to add description...'}
                    </p>
                  )}
                </div>

                {/* Related Tables */}
                {selected.related_tables.filter(Boolean).length > 0 && (
                  <div className="form-group">
                    <label>Related Tables</label>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                      {selected.related_tables.filter(Boolean).map((t) => (
                        <Link key={t} to={`/tables/${t}`} className="tag tag-blue" onClick={() => setSelected(null)}>
                          {t}
                        </Link>
                      ))}
                    </div>
                  </div>
                )}

                {/* Concepts */}
                {selected.concepts.filter(Boolean).length > 0 && (
                  <div className="form-group">
                    <label>Concepts</label>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                      {selected.concepts.filter(Boolean).map((c) => (
                        <span key={c} className="tag tag-green">{c}</span>
                      ))}
                    </div>
                  </div>
                )}

                {/* Metadata Keys */}
                {selected.metadata_keys.filter((mk) => mk.name).length > 0 && (
                  <div className="form-group">
                    <label>Metadata Keys</label>
                    <table className="data-table" style={{ fontSize: 13 }}>
                      <thead>
                        <tr>
                          <th>Name</th>
                          <th>Data Type</th>
                          <th>Filterable</th>
                        </tr>
                      </thead>
                      <tbody>
                        {selected.metadata_keys.filter((mk) => mk.name).map((mk) => (
                          <tr key={mk.name}>
                            <td><strong>{mk.name}</strong></td>
                            <td><code style={{ fontSize: 12 }}>{mk.data_type}</code></td>
                            <td>{mk.filterable ? <span className="tag tag-green">yes</span> : <span className="tag" style={{ background: 'var(--bg-alt)' }}>no</span>}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                <div className="modal-actions">
                  <button className="btn btn-ghost" onClick={() => setSelected(null)}>Close</button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {toast && <div className={`toast toast-${toast.type}`}>{toast.msg}</div>}
    </>
  )
}
