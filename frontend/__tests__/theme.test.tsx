import { render, screen, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import {
  ThemeProvider,
  THEME_INIT_SCRIPT,
  THEME_LABELS,
  THEME_STORAGE_KEY,
  useTheme,
} from '@/app/components/ThemeProvider';

/**
 * M12E — Claro / Oscuro / Automático.
 *
 * Lo interesante no es que un botón cambie una clase. Es que «automático»
 * signifique automático de verdad —que reaccione al sistema con la pestaña ya
 * abierta—, que la preferencia sobreviva a una recarga, y que el modo por
 * defecto NO sea el de una tienda concreta.
 */

let media: {
  matches: boolean;
  listeners: Array<() => void>;
  fire: () => void;
};

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute('data-theme');
  media = {
    matches: false,
    listeners: [],
    fire() {
      this.listeners.forEach((l) => l());
    },
  };
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: () => ({
      get matches() {
        return media.matches;
      },
      addEventListener: (_: string, l: () => void) => media.listeners.push(l),
      removeEventListener: (_: string, l: () => void) => {
        media.listeners = media.listeners.filter((x) => x !== l);
      },
    }),
  });
});

function Probe() {
  const { choice, resolved, setChoice } = useTheme();
  return (
    <div>
      <span data-testid="choice">{choice}</span>
      <span data-testid="resolved">{resolved}</span>
      <button onClick={() => setChoice('light')}>claro</button>
      <button onClick={() => setChoice('dark')}>oscuro</button>
      <button onClick={() => setChoice('system')}>auto</button>
    </div>
  );
}

describe('el modo por defecto', () => {
  it('es automático, no el tema de una tienda concreta', () => {
    // Este frontend sirve a cualquier tenant. Arrancar en oscuro porque el
    // piloto es oscuro convertiría su marca en el default de la plataforma.
    render(<ThemeProvider><Probe /></ThemeProvider>);
    expect(screen.getByTestId('choice')).toHaveTextContent('system');
  });

  it('automático sigue al sistema: claro', () => {
    media.matches = false;
    render(<ThemeProvider><Probe /></ThemeProvider>);
    expect(screen.getByTestId('resolved')).toHaveTextContent('light');
  });

  it('automático sigue al sistema: oscuro', () => {
    media.matches = true;
    render(<ThemeProvider><Probe /></ThemeProvider>);
    expect(screen.getByTestId('resolved')).toHaveTextContent('dark');
  });
});

describe('automático reacciona en vivo', () => {
  it('cambia cuando el sistema cambia, sin recargar', async () => {
    // Sin esto, «automático» sólo sería «automático la próxima vez que
    // recargues» — que es otra cosa.
    media.matches = false;
    render(<ThemeProvider><Probe /></ThemeProvider>);
    expect(screen.getByTestId('resolved')).toHaveTextContent('light');

    await act(async () => {
      media.matches = true;
      media.fire();
    });
    expect(screen.getByTestId('resolved')).toHaveTextContent('dark');
  });

  it('deja de escuchar al sistema cuando se elige un modo fijo', async () => {
    media.matches = false;
    render(<ThemeProvider><Probe /></ThemeProvider>);
    await userEvent.click(screen.getByText('oscuro'));

    await act(async () => {
      media.matches = false;
      media.fire();
    });
    expect(screen.getByTestId('resolved')).toHaveTextContent('dark');
  });
});

describe('la preferencia persiste', () => {
  it('guarda la elección con una clave neutra', async () => {
    render(<ThemeProvider><Probe /></ThemeProvider>);
    await userEvent.click(screen.getByText('claro'));
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('light');
    // Neutra porque el frontend es multiempresa: `blackdog-theme` sería el
    // nombre de una tienda en la configuración de todas.
    expect(THEME_STORAGE_KEY).toBe('ui-theme');
    expect(THEME_STORAGE_KEY).not.toContain('blackdog');
  });

  it('la recupera al montar de nuevo', () => {
    localStorage.setItem(THEME_STORAGE_KEY, 'dark');
    render(<ThemeProvider><Probe /></ThemeProvider>);
    expect(screen.getByTestId('choice')).toHaveTextContent('dark');
    expect(screen.getByTestId('resolved')).toHaveTextContent('dark');
  });

  it('un valor corrupto no rompe nada', () => {
    localStorage.setItem(THEME_STORAGE_KEY, 'morado');
    render(<ThemeProvider><Probe /></ThemeProvider>);
    expect(screen.getByTestId('choice')).toHaveTextContent('system');
  });
});

describe('el DOM refleja el tema', () => {
  it('escribe data-theme y color-scheme', async () => {
    render(<ThemeProvider><Probe /></ThemeProvider>);
    await userEvent.click(screen.getByText('oscuro'));
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark');
    // `color-scheme` es lo que hace que inputs, selects y scrollbars nativos
    // sigan el modo. Sin él, un formulario oscuro sale con el select blanco.
    expect(document.documentElement.style.colorScheme).toBe('dark');
  });
});

describe('el script anti-flash', () => {
  it('resuelve el tema antes del primer paint', () => {
    // Leerlo en un useEffect significa pintar con el tema equivocado y
    // corregir: es el parpadeo blanco al recargar en oscuro.
    expect(THEME_INIT_SCRIPT).toContain('prefers-color-scheme');
    expect(THEME_INIT_SCRIPT).toContain('data-theme');
    expect(THEME_INIT_SCRIPT).toContain('colorScheme');
  });

  it('usa la misma clave que el provider', () => {
    expect(THEME_INIT_SCRIPT).toContain(JSON.stringify(THEME_STORAGE_KEY));
  });

  it('está envuelto en try/catch', () => {
    // En una ventana privada localStorage puede lanzar, y una preferencia de
    // color no puede tumbar la página.
    expect(THEME_INIT_SCRIPT).toContain('try{');
    expect(THEME_INIT_SCRIPT).toContain('catch');
  });
});

describe('las etiquetas están en español', () => {
  it('los tres modos', () => {
    expect(THEME_LABELS.system).toBe('Automático');
    expect(THEME_LABELS.light).toBe('Claro');
    expect(THEME_LABELS.dark).toBe('Oscuro');
  });
});
