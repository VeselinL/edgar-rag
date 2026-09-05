import { useId, useState } from 'react'
import type { NarrativeSource as NarrativeSourceType, Source, SourceStatus, TableSource as TableSourceType, UploadedSource as UploadedSourceType, WebSource as WebSourceType } from '../types'
import type { Language } from '../i18n'
import { t } from '../i18n'

type FilingSource = NarrativeSourceType | TableSourceType

function SourceHeading({ source }: { source: FilingSource }) {
  return (
    <div className="source-heading">
      <strong>{source.company} ({source.ticker})</strong>
      <span>{source.filing_year} 10-K · {source.section}</span>
    </div>
  )
}

function SourceLink({ source, language }: { source: FilingSource; language: Language }) {
  if (!source.source_url) return null
  return <a href={source.source_url} target="_blank" rel="noreferrer">{t(language, 'openSecFiling')}</a>
}

export function WebSource({ source, language }: { source: WebSourceType; language: Language }) {
  return (
    <article className="source-card">
      <div className="source-heading">
        <strong>{source.title}</strong>
        <span>{source.publisher} · {t(language, 'retrieved')} {new Date(source.retrieved_at).toLocaleString(language === 'sr' ? 'sr-RS' : 'en-US')}</span>
      </div>
      <p className="source-text">{source.excerpt}</p>
      <a href={source.source_url} target="_blank" rel="noreferrer">{t(language, 'openWebSource')}</a>
    </article>
  )
}

export function UploadedSource({ source, language }: { source: UploadedSourceType; language: Language }) {
  return (
    <article className="source-card">
      <div className="source-heading">
        <strong>{source.filename}</strong>
        <span>{t(language, 'chatUpload')}{source.page_number ? ` · ${t(language, 'page')} ${source.page_number}` : ''}</span>
      </div>
      <p className="source-text">{source.excerpt}</p>
    </article>
  )
}

export function NarrativeSource({ source, language }: { source: NarrativeSourceType; language: Language }) {
  return (
    <article className="source-card">
      <SourceHeading source={source} />
      <p className="source-text">{source.text}</p>
      <SourceLink source={source} language={language} />
    </article>
  )
}

function isNumericColumn(unit: string | undefined): boolean {
  if (!unit) return false
  return unit !== 'text' && unit !== 'date' && unit !== 'identifier'
}

export function TableSource({ source, language }: { source: TableSourceType; language: Language }) {
  const rectangular = source.headers.length > 0 && source.rows.every((row) => row.length === source.headers.length)
  return (
    <article className="source-card">
      <SourceHeading source={source} />
      {source.title && <h4>{source.title}</h4>}
      {source.units && <p className="table-units">{t(language, 'units')}: {source.units}</p>}
      {rectangular ? (
        <div className="table-scroll" tabIndex={0} role="region" aria-label={`${t(language, 'scrollableTable')}: ${source.title ?? source.section}`}>
          <table>
            <thead>
              <tr>
                {source.headers.map((header, index) => (
                  <th key={`${header}-${index}`} scope="col" className={isNumericColumn(source.column_units?.[index]) ? 'number' : undefined}>{header}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {source.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((cell, cellIndex) => (
                    <td key={cellIndex} className={isNumericColumn(source.column_units?.[cellIndex]) ? 'number' : undefined}>{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="source-warning">{t(language, 'tableUnavailable')}</p>}
      <SourceLink source={source} language={language} />
    </article>
  )
}

export function Sources({ sources, sourceStatus, malformedCount, language }: { sources: Source[]; sourceStatus: SourceStatus; malformedCount: number; language: Language }) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  if (sources.length === 0) {
    return (
      <div>
        <p className="source-empty">{t(language, 'noReferences')}</p>
        {sourceStatus === 'cited_with_unrenderable_items' && malformedCount > 0 && (
          <p className="source-warning">{t(language, 'citedSourceUnavailable')}</p>
        )}
      </div>
    )
  }
  return (
    <div className="sources">
      <button className="sources-button" type="button" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen((value) => !value)}>
        {open ? t(language, 'hide') : t(language, 'view')} {t(language, 'sources').toLowerCase()} ({sources.length})
      </button>
      {open && (
        <div className="sources-panel" id={panelId}>
          {sources.map((source, index) => source.content_type === 'web'
            ? <WebSource key={`${source.source_url}-${index}`} source={source} language={language} />
            : source.content_type === 'upload'
              ? <UploadedSource key={`${source.document_id}-${source.page_number ?? 0}-${index}`} source={source} language={language} />
            : source.content_type === 'table'
              ? <TableSource key={`${source.ticker}-${source.section}-${index}`} source={source} language={language} />
              : <NarrativeSource key={`${source.ticker}-${source.section}-${index}`} source={source} language={language} />)}
          {malformedCount > 0 && <p className="source-warning">{malformedCount === 1 ? t(language, 'oneSourceUnavailable') : `${malformedCount} ${t(language, 'sourcesUnavailable')}`}</p>}
        </div>
      )}
    </div>
  )
}
