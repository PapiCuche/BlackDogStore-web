import {
  EVIDENCE_STAGES,
  groupByStage,
  stageCapability,
  stageLabel,
  type Evidence,
} from '@/app/admin/service/components/EvidenceGallery';

/**
 * M12D — la galería, desde el lado del navegador.
 *
 * Lo que se prueba no es que pinte tarjetas. Es lo que un descuido aquí haría
 * de verdad: ofrecer un botón para una etapa cuya autoridad el usuario no
 * tiene, construir una URL de imagen desde algo que el servidor no envió, o
 * llamar "Eliminar" a algo que no elimina.
 */

function ev(partial: Partial<Evidence>): Evidence {
  return {
    id: 1,
    stage: 'intake',
    visibility: 'internal',
    mime_type: 'image/webp',
    byte_size: 120_000,
    width: 1600,
    height: 1200,
    created_at: '2026-09-03T10:00:00Z',
    uploaded_by: 'tecnico',
    voided_at: null,
    void_reason: null,
    ...partial,
  };
}

describe('las etapas', () => {
  it('cubren exactamente las ocho del backend', () => {
    expect(EVIDENCE_STAGES.map((s) => s.value)).toEqual([
      'intake',
      'diagnosis',
      'repair_before',
      'repair_during',
      'repair_after',
      'quality',
      'delivery',
      'other',
    ]);
  });

  it('cada una nombra la capacidad que el servidor va a pedir', () => {
    // Si esta tabla se desincroniza del backend, la interfaz ofrece un botón
    // que produce un 403 — o esconde uno que sí funcionaría.
    expect(stageCapability('diagnosis')).toBe('service.diagnostic.manage');
    expect(stageCapability('delivery')).toBe('service.delivery.manage');
    expect(stageCapability('repair_during')).toBe('service.repair.manage');
    expect(stageCapability('other')).toBe('service.orders.manage');
  });

  it('no inventa una capacidad para una etapa desconocida', () => {
    expect(stageCapability('warranty')).toBeNull();
  });

  it('ninguna etapa pide una capability de evidencias', () => {
    // M12D no creó ninguna. Que apareciera aquí sería la señal de que alguien
    // la añadió al backend.
    for (const s of EVIDENCE_STAGES) {
      expect(s.capability).not.toContain('evidence');
    }
  });

  it('traduce la etapa a algo legible, y no rompe con una desconocida', () => {
    expect(stageLabel('repair_after')).toBe('Después de reparar');
    expect(stageLabel('desconocida')).toBe('desconocida');
  });
});

describe('el agrupado', () => {
  it('sigue el orden del ciclo, no el alfabético', () => {
    const groups = groupByStage([
      ev({ id: 1, stage: 'delivery' }),
      ev({ id: 2, stage: 'intake' }),
      ev({ id: 3, stage: 'quality' }),
    ]);
    expect(groups.map((g) => g.stage.value)).toEqual([
      'intake',
      'quality',
      'delivery',
    ]);
  });

  it('omite las etapas sin fotos en lugar de dibujarlas vacías', () => {
    const groups = groupByStage([ev({ stage: 'intake' })]);
    expect(groups).toHaveLength(1);
  });

  it('conserva todas las evidencias de una etapa', () => {
    const groups = groupByStage([
      ev({ id: 1, stage: 'intake' }),
      ev({ id: 2, stage: 'intake' }),
    ]);
    expect(groups[0].items.map((i) => i.id)).toEqual([1, 2]);
  });

  it('una anulada sigue apareciendo, porque no se borró', () => {
    const groups = groupByStage([
      ev({ id: 9, stage: 'intake', voided_at: '2026-09-03T11:00:00Z' }),
    ]);
    expect(groups[0].items[0].voided_at).not.toBeNull();
  });
});

describe('lo que el componente NO hace', () => {
  const source = jest
    .requireActual('fs')
    .readFileSync(
      jest
        .requireActual('path')
        .join(process.cwd(), 'app/admin/service/components/EvidenceGallery.tsx'),
      'utf8',
    ) as string;

  // Los comentarios se quitan antes de mirar: este archivo explica lo que no
  // hace, y un escaneo de texto casaría con su propia explicación.
  const code = source
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');

  it('no pinta HTML del usuario', () => {
    expect(code).not.toContain('dangerouslySetInnerHTML');
    expect(code).not.toContain('eval(');
    expect(code).not.toContain('new Function');
  });

  it('no lee una clave de storage ni una URL firmada del servidor', () => {
    // El servidor manda `id`. La imagen se pide al endpoint de contenido, que
    // vuelve a autorizar; una URL que llegara por la red sería una URL en la
    // que alguien más decidió a dónde apunta.
    expect(code).not.toContain('storage_key');
    expect(code).not.toContain('signed_url');
    expect(code).toContain('/content/');
  });

  it('libera el object URL de la previsualización', () => {
    // Sin esto, una sesión larga de subidas retiene cada imagen en memoria.
    expect(code).toContain('revokeObjectURL');
  });

  it('no llama "Eliminar" a una acción que no elimina', () => {
    expect(code).toContain('Anular');
    expect(code).not.toMatch(/>\s*Eliminar\s*</);
  });

  it('manda una clave de idempotencia al subir', () => {
    expect(code).toContain('Idempotency-Key');
  });
});
