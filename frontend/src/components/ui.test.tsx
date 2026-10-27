import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Segmented, SubNav } from './ui';

afterEach(cleanup);

describe('Segmented', () => {
  it('exposes the selected option to assistive tech and reports changes', () => {
    const onChange = vi.fn();
    render(
      <Segmented
        label="Modo"
        value="paper"
        onChange={onChange}
        options={[
          { value: 'paper', label: 'Simulado' },
          { value: 'real', label: 'Testnet' },
        ]}
      />,
    );

    expect(screen.getByRole('group', { name: 'Modo' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Simulado' }).getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: 'Testnet' }));
    expect(onChange).toHaveBeenCalledWith('real');
  });

  it('blocks changes while disabled', () => {
    const onChange = vi.fn();
    render(
      <Segmented label="Modo" value="a" disabled onChange={onChange} options={[{ value: 'b', label: 'B' }]} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'B' }));
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe('SubNav', () => {
  it('marks the active tab', () => {
    render(
      <SubNav
        label="Secciones"
        value="ia"
        onChange={() => undefined}
        items={[
          { id: 'cuenta', label: 'Cuenta' },
          { id: 'ia', label: 'IA' },
        ]}
      />,
    );
    expect(screen.getByRole('tab', { name: 'IA' }).getAttribute('aria-selected')).toBe('true');
    expect(screen.getByRole('tab', { name: 'Cuenta' }).getAttribute('aria-selected')).toBe('false');
  });
});
