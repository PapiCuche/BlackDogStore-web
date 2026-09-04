# Assets de marca del tenant

Los seis logotipos por contraste que consume `BrandLogo`:

```
logo-vertical-on-light.png     logo-vertical-on-dark.png
logo-horizontal-on-light.png   logo-horizontal-on-dark.png
logo-isotype-on-light.png      logo-isotype-on-dark.png
```

`on-light` es OSCURO (va sobre fondo claro) y `on-dark` es CLARO. No es una
convención de nombres: `__tests__/logo-assets.test.ts` abre cada PNG y mide la
luminancia mediana de sus píxeles visibles. Sustituir un archivo por su
contrario no rompe ninguna importación — sólo hace desaparecer el logotipo — y
por eso se comprueba.

Los tres originales (`logo.png`, `logo-icon.png`, `logo-text.png`) se conservan
sin tocar: son el material del que salieron las variantes.

## Qué NO va aquí

Este directorio tenía doce maquetas de un prototipo anterior —azul marino y
turquesa, «BlackDog Store» mal escrito y un precio sin respaldo incrustado en
la imagen— y un README que describía un carrusel de portada que ningún código
leía. Se retiraron en la auditoría de frontend.

Las imágenes comerciales del tenant no van en el repositorio: van en su
configuración, que es lo que permite que cada empresa tenga las suyas.
