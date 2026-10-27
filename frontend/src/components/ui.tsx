import type { ReactNode } from 'react';
import { Icon, type IconName } from './Icon';

export function Card({
  title,
  subtitle,
  icon,
  actions,
  children,
  className = '',
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  icon?: IconName;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          <div>
            {title && (
              <h2 className="card-title">
                {icon && <Icon name={icon} />}
                {title}
              </h2>
            )}
            {subtitle && <p className="card-sub">{subtitle}</p>}
          </div>
          {actions && <div className="row">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({
  label,
  value,
  sub,
  tone = '',
}: {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  tone?: string;
}) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${tone}`}>{value}</div>
      {sub !== undefined && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

type PillTone = 'default' | 'solid' | 'pos' | 'neg' | 'warn';

export function Pill({
  tone = 'default',
  dot = false,
  live = false,
  children,
  title,
}: {
  tone?: PillTone;
  dot?: boolean;
  live?: boolean;
  children: ReactNode;
  title?: string;
}) {
  const toneClass = tone === 'default' ? '' : `pill-${tone}`;
  return (
    <span className={`pill ${toneClass}`} title={title}>
      {dot && <span className={`dot ${live ? 'dot-live' : ''}`} aria-hidden="true" />}
      <span className="pill-text">{children}</span>
    </span>
  );
}

export function Notice({
  tone = 'info',
  children,
}: {
  tone?: 'info' | 'warn' | 'error' | 'ok';
  children: ReactNode;
}) {
  const icon: IconName = tone === 'ok' ? 'check' : tone === 'info' ? 'info' : 'alert';
  return (
    <div className={`notice ${tone === 'info' ? '' : `notice-${tone}`}`} role={tone === 'error' ? 'alert' : undefined}>
      <Icon name={icon} size={16} />
      <div>{children}</div>
    </div>
  );
}

export function Disclosure({
  summary,
  children,
  defaultOpen = false,
}: {
  summary: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <details className="disclosure" open={defaultOpen}>
      <summary>
        <span>{summary}</span>
        <Icon name="chevronDown" size={16} />
      </summary>
      <div className="disclosure-body">{children}</div>
    </details>
  );
}

export interface SegmentOption<T extends string> {
  value: T;
  label: ReactNode;
  title?: string;
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  disabled = false,
  block = false,
  label,
}: {
  options: ReadonlyArray<SegmentOption<T>>;
  value: T;
  onChange: (value: T) => void;
  disabled?: boolean;
  block?: boolean;
  label: string;
}) {
  return (
    <div className={`seg ${block ? 'seg-block' : ''}`} role="group" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={option.value === value}
          title={option.title}
          disabled={disabled}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function SubNav<T extends string>({
  items,
  value,
  onChange,
  label,
}: {
  items: ReadonlyArray<{ id: T; label: string }>;
  value: T;
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div className="subnav" role="tablist" aria-label={label}>
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          role="tab"
          aria-selected={item.id === value}
          onClick={() => onChange(item.id)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

export function PageHead({
  title,
  description,
  actions,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Skeleton({ height = 14, width = '100%' }: { height?: number; width?: number | string }) {
  return <div className="skeleton" style={{ height, width }} aria-hidden="true" />;
}
