import styles from './DetailPanel.module.css'

export interface DetailField {
  label: string
  value: string
  tone?: 'good' | 'bad' | 'unknown' | 'neutral'
}

export interface DetailSection {
  title: string
  fields: DetailField[]
}

/** The shared per-player detail panel #109 and #110 both need — generic over whatever fields a
 * page wants to show (projection sources, matchup, role trend, ROS value, ...) rather than
 * hardcoded to one page's shape. Native <details>/<summary>: no JS, accessible, no animation
 * requirement in the ticket. */
export function DetailPanel({
  title,
  sections,
  defaultOpen = false,
}: {
  title: string
  sections: DetailSection[]
  defaultOpen?: boolean
}) {
  return (
    <details className={styles.panel} open={defaultOpen}>
      <summary className={styles.summary}>{title}</summary>
      <div className={styles.body}>
        {sections.map((section) => (
          <section key={section.title} className={styles.section}>
            <h3 className={`label ${styles.sectionTitle}`}>{section.title}</h3>
            <dl className={styles.fields}>
              {section.fields.map((field) => (
                <div className={styles.field} key={field.label}>
                  <dt className={styles.fieldLabel}>{field.label}</dt>
                  <dd className={`mono ${styles.fieldValue}`} data-tone={field.tone ?? 'neutral'}>
                    {field.tone === 'unknown' ? (
                      <span className="unknown">{field.value}</span>
                    ) : (
                      field.value
                    )}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        ))}
      </div>
    </details>
  )
}
