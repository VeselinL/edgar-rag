import { useId, useState } from 'react'
import type { NarrativeSource as NarrativeSourceType, Source, SourceStatus, TableSource as TableSourceType } from '../types'

function SourceHeading({ source }: { source: Source }) {
  return (
    <div className="source-heading">
      <strong>{source.company} ({source.ticker})</strong>
      <span>{source.filing_year} 10-K · {source.section}</span>
    </div>
  )
}

function SourceLink({ source }: { source: Source }) {
  if (!source.source_url) return null
  return <a href={source.source_url} target="_blank" rel="noreferrer">Open SEC filing</a>
}

export function NarrativeSource({ source }: { source: NarrativeSourceType }) {
  return (
    <article className="source-card">
      <SourceHeading source={source} />
      <p className="source-text">{source.text}</p>
      <SourceLink source={source} />
    </article>
  )
}

function isNumericColumn(unit: string | undefined): boolean {
  if (!unit) return false
  return unit !== 'text' && unit !== 'date' && unit !== 'identifier'
}

export function TableSource({ source }: { source: TableSourceType }) {
  const rectangular = source.headers.length > 0 && source.rows.every((row) => row.length === source.headers.length)
  return (
    <article className="source-card">
      <SourceHeading source={source} />
      {source.title && <h4>{source.title}</h4>}
      {source.units && <p className="table-units">Units: {source.units}</p>}
      {rectangular ? (
        <div className="table-scroll" tabIndex={0} role="region" aria-label={`Scrollable table: ${source.title ?? source.section}`}>
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
      ) : <p className="source-warning">This table source could not be displayed.</p>}
      <SourceLink source={source} />
    </article>
  )
}

export function Sources({ sources, sourceStatus, malformedCount }: { sources: Source[]; sourceStatus: SourceStatus; malformedCount: number }) {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  if (sources.length === 0) {
    return (
      <div>
        <p className="source-empty">No source references were available for this answer.</p>
        {sourceStatus === 'cited_with_unrenderable_items' && malformedCount > 0 && (
          <p className="source-warning">The cited source could not be displayed.</p>
        )}
      </div>
    )
  }
  return (
    <div className="sources">
      <button className="sources-button" type="button" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen((value) => !value)}>
        {open ? 'Hide' : 'View'} sources ({sources.length})
      </button>
      {open && (
        <div className="sources-panel" id={panelId}>
          {sources.map((source, index) => source.content_type === 'table'
            ? <TableSource key={`${source.ticker}-${source.section}-${index}`} source={source} />
            : <NarrativeSource key={`${source.ticker}-${source.section}-${index}`} source={source} />)}
          {malformedCount > 0 && <p className="source-warning">{malformedCount === 1 ? 'One source could not be displayed.' : `${malformedCount} sources could not be displayed.`}</p>}
        </div>
      )}
    </div>
  )
}
