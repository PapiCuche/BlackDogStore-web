"use client";

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import { Button, ErrorNote, Field, Panel } from './ServiceUi';
import { DeviceRegistration } from './DeviceRegistration';
import { createServiceOrder, fetchCustomerDevices, fetchServiceTechnicians,
  mayAssignTechnician, searchServiceCustomers, type ServiceAssignmentCandidate,
  type ServiceContext, type ServiceOrderDetail } from '../../../lib/service-console';

/**
 * Reception of a device. The same form serves the workshop and the till.
 *
 * THE TECHNICIAN. Whoever may assign can name the technician here, and the
 * order is then created and assigned in ONE request. The candidates are the
 * ones the server returns for the chosen branch: this screen cannot work out
 * who is staff, and an id it made up would be answered "not found". The till
 * passes `requireTechnician`, because a device taken at the counter with nobody
 * responsible for it is the case this flow exists to prevent.
 */
export function ServiceIntake({ slug, context, may, onCreated, requireTechnician = false,
  submitLabel = 'Registrar recepción', onAnother }: {
  slug: string; context: ServiceContext; may: (capability: string) => boolean;
  onCreated?: (id: number) => void;
  requireTechnician?: boolean; submitLabel?: string;
  /** Offered after a success, to start the next reception. */
  onAnother?: () => void;
}) {
  const [search, setSearch] = useState('');
  const [customers, setCustomers] = useState<{ id: number; display_name: string }[]>([]);
  const [devices, setDevices] = useState<{ id: number; display_name: string }[]>([]);
  const [customer, setCustomer] = useState('');
  const [device, setDevice] = useState('');
  const [branch, setBranch] = useState(context.available_branches.length === 1 ? String(context.available_branches[0].id) : '');
  const [issue, setIssue] = useState('');
  const [condition, setCondition] = useState('');
  const [accessories, setAccessories] = useState('');
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState<ServiceOrderDetail | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [technician, setTechnician] = useState('');
  // `null` until the server has answered for this branch: "nobody" is a fact
  // only it can state, and not the same as "still asking".
  const [technicians, setTechnicians] = useState<ServiceAssignmentCandidate[] | null>(null);
  const locked = useRef(false);
  const canAssign = mayAssignTechnician(may);

  // Candidates belong to a branch: another branch, another list, and whoever
  // was picked for the previous one is no longer a valid choice.
  useEffect(() => {
    if (!canAssign || !branch) return;
    let stale = false;
    fetchServiceTechnicians(slug, Number(branch))
      .then((data) => { if (!stale) setTechnicians(data.candidates); })
      .catch((err) => { if (!stale) { setTechnicians(null); setError(err); } });
    return () => { stale = true; };
  }, [slug, branch, canAssign]);

  async function run(action: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError(null);
    try { await action(); } catch (err) { setError(err); }
    finally { locked.current = false; setBusy(false); }
  }

  const selectStyle = 'mt-1 w-full rounded-lg border border-bd-border bg-surface p-2 text-foreground';
  return <Panel title="Nueva orden de servicio" subtitle="Registra la recepción del equipo en una sucursal autorizada.">
    <div className="space-y-4">
      <ErrorNote error={error} />
      {created ? <div className="space-y-3 text-sm">
        <p>Orden <strong>{created.number}</strong> creada.</p>
        {created.technician_name ? <p className="text-muted">Técnico asignado: {created.technician_name}. Ya la ve en «Mis reparaciones».</p> : null}
        <div className="flex flex-wrap items-center gap-3">
          <Link className="rounded-lg border border-bd-border px-4 py-2 text-sm" href={`/admin/service/orders/${created.id}`}>Abrir orden</Link>
          {onAnother ? <Button onClick={onAnother}>Nuevo servicio</Button> : null}
        </div>
      </div> : <>
        <Field label="Buscar por nombre o documento" value={search} onChange={setSearch} />
        <Button disabled={busy} onClick={() => void run(async () => {
          setCustomers((await searchServiceCustomers(slug, search)).results);
        })}>Buscar cliente</Button>
        {may('service.customers.manage') ? <Link className="ml-3 text-xs underline" href="/admin/customers">Registrar cliente en Clientes</Link> : null}
        <label className="block text-xs text-muted">Cliente
          <select className={selectStyle} value={customer} disabled={busy} onChange={(event) => {
            const id = event.target.value;
            setCustomer(id); setDevice(''); setDevices([]);
            if (id) void run(async () => { setDevices((await fetchCustomerDevices(slug, Number(id))).results); });
          }}><option value="">Selecciona cliente</option>{customers.map((row) => <option key={row.id} value={row.id}>{row.display_name}</option>)}</select>
        </label>
        <label className="block text-xs text-muted">Equipo
          <select className={selectStyle} value={device} disabled={busy || !customer} onChange={(event) => setDevice(event.target.value)}>
            <option value="">Selecciona equipo</option>{devices.map((row) => <option key={row.id} value={row.id}>{row.display_name}</option>)}
          </select>
        </label>
        {customer && may('service.devices.manage') ? <details>
          <summary className="cursor-pointer text-sm">Registrar otro equipo</summary>
          {/* `key`: otro cliente, otro formulario. Lo escrito para uno no se
              arrastra al siguiente. */}
          <DeviceRegistration
            key={customer}
            slug={slug}
            customerId={Number(customer)}
            deviceTypes={context.device_types ?? []}
            disabled={busy}
            onRegistered={(row) => {
              setDevices((previous) => (previous.some((item) => item.id === row.id) ? previous : [...previous, row]));
              setDevice(String(row.id));
            }}
          />
        </details> : null}
        <label className="block text-xs text-muted">Sucursal<select className={selectStyle} value={branch} disabled={busy} onChange={(event) => { setBranch(event.target.value); setTechnician(''); setTechnicians(null); }}>
          <option value="">Selecciona sucursal</option>{context.available_branches.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}
        </select></label>
        <Field label="Falla reportada" value={issue} onChange={setIssue} textarea />
        <Field label="Estado físico" value={condition} onChange={setCondition} />
        <Field label="Accesorios recibidos" value={accessories} onChange={setAccessories} />
        {canAssign ? <label className="block text-xs text-muted">Técnico asignado
          <select className={selectStyle} value={technician} disabled={busy || !branch} onChange={(event) => setTechnician(event.target.value)}>
            <option value="">{requireTechnician ? 'Selecciona técnico' : 'Sin asignar por ahora'}</option>
            {(technicians ?? []).map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}
          </select>
        </label> : null}
        {requireTechnician && branch && canAssign && technicians?.length === 0 ? <p className="text-xs text-muted">
          Ningún técnico alcanza esta sucursal. Elige otra o pide que asignen uno.</p> : null}
        <Button disabled={busy || !customer || !device || !branch || !issue.trim() || (requireTechnician && !technician)} onClick={() => void run(async () => {
          const order = await createServiceOrder(slug, { customer_id: Number(customer), device_id: Number(device),
            branch_id: Number(branch), reported_issue: issue, physical_condition: condition, received_accessories: accessories,
            ...(technician ? { technician_id: Number(technician) } : {}) });
          setCreated(order); onCreated?.(order.id);
        })}>{submitLabel}</Button>
      </>}
    </div>
  </Panel>;
}
