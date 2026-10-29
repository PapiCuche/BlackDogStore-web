import { assertSafeTarget, unsafeTarget } from "@/e2e/inventory-first-guard";

/** INV-QA-E: la guarda que protege las bases vivas del ayudante de siembra y limpieza del E2E. */
const ok = { debug: true, host: "localhost", name: "blackdog_qae_y", engine: "django.db.backends.postgresql" };

describe("guarda del ayudante E2E", () => {
  it("acepta una base local desechable con DEBUG encendido", () => {
    expect(unsafeTarget(ok)).toBeNull();
    expect(unsafeTarget({ ...ok, host: "127.0.0.1", name: "test_blackdog" })).toBeNull();
    expect(unsafeTarget({ ...ok, host: "::1", name: "blackdog_e2e_b" })).toBeNull();
    expect(unsafeTarget({ ...ok, host: "" })).toBeNull();
    expect(unsafeTarget({ debug: true, host: "", name: "/tmp/e2e/db.sqlite3", engine: "django.db.backends.sqlite3" })).toBeNull();
  });

  it("se niega con DEBUG apagado", () => {
    expect(unsafeTarget({ ...ok, debug: false })).toMatch(/DEBUG/);
    expect(() => assertSafeTarget({ ...ok, debug: false })).toThrow(/se niega a correr/);
  });

  it("se niega con un servidor que no es local", () => {
    for (const host of ["db.produccion.example", "54.94.236.23", "localhost.evil.example", "10.0.0.5"]) {
      expect(unsafeTarget({ ...ok, host })).toMatch(/no es local/);
    }
  });

  it("se niega con una base que no se declara desechable, salvo que el operador la acepte por nombre exacto", () => {
    expect(unsafeTarget({ ...ok, name: "blackdog" })).toMatch(/no se declara desechable/);
    expect(unsafeTarget({ ...ok, name: "blackdog_dev" })).toMatch(/no se declara desechable/);
    expect(unsafeTarget({ ...ok, name: "blackdog" }, "blackdog")).toBeNull();
    expect(unsafeTarget({ ...ok, name: "blackdog_dev" }, "blackdog")).not.toBeNull();
    expect(unsafeTarget({ ...ok, name: "" })).toMatch(/nombre/);
  });

  it("un servidor remoto no se salva por llamarse e2e", () => {
    expect(unsafeTarget({ ...ok, host: "e2e.example.com", name: "e2e" })).toMatch(/no es local/);
  });
});
