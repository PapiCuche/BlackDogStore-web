"use client";

import Link from 'next/link';
import { useRef, useState } from 'react';
import { Button, ErrorNote, Field, Panel } from './ServiceUi';
import { createServiceDevice, createServiceOrder, fetchCustomerDevices,
  searchServiceCustomers, type ServiceContext } from '../../../lib/service-console';

export function ServiceIntake({ slug, context, may, onCreated }: {
  slug: string; context: ServiceContext; may: (capability: string) => boolean;
  onCreated: (id: number) => void;
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
  const [brand, setBrand] = useState('');
  const [model, setModel] = useState('');
  const [deviceType, setDeviceType] = useState('');
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const locked = useRef(false);

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
      {created ? <p>Orden creada.</p> : <>
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
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <label className="text-xs text-muted">Tipo de equipo<select className={selectStyle} value={deviceType} onChange={(event) => setDeviceType(event.target.value)}>
              <option value="">Selecciona tipo</option>{context.device_types?.map((row) => <option key={row.value} value={row.value}>{row.label}</option>)}
            </select></label>
            <Field label="Marca" value={brand} onChange={setBrand} />
            <Field label="Modelo" value={model} onChange={setModel} />
            <Button disabled={busy || !deviceType || !brand.trim() || !model.trim()} onClick={() => void run(async () => {
              const row = await createServiceDevice(slug, { customer_id: Number(customer), device_type: deviceType, brand, model });
              setDevices((previous) => [...previous, row]); setDevice(String(row.id));
            })}>Guardar equipo</Button>
          </div>
        </details> : null}
        <label className="block text-xs text-muted">Sucursal<select className={selectStyle} value={branch} disabled={busy} onChange={(event) => setBranch(event.target.value)}>
          <option value="">Selecciona sucursal</option>{context.available_branches.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}
        </select></label>
        <Field label="Falla reportada" value={issue} onChange={setIssue} textarea />
        <Field label="Estado físico" value={condition} onChange={setCondition} />
        <Field label="Accesorios recibidos" value={accessories} onChange={setAccessories} />
        <Button disabled={busy || !customer || !device || !branch || !issue.trim()} onClick={() => void run(async () => {
          const order = await createServiceOrder(slug, { customer_id: Number(customer), device_id: Number(device),
            branch_id: Number(branch), reported_issue: issue, physical_condition: condition, received_accessories: accessories });
          setCreated(true); onCreated(order.id);
        })}>Registrar recepción</Button>
      </>}
    </div>
  </Panel>;
}
