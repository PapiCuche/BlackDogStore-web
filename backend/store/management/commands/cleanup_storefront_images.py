"""
Retira las imágenes de la tienda que se subieron y nunca se colocaron.

Subir no coloca. Quien sube una imagen en el panel y cierra el formulario sin
guardar deja un archivo al que ningún hueco apunta, y que por tanto ningún hueco
soltará nunca. Lo que se sustituye o se quita desde el panel se retira en el
momento; esto es para lo otro.

SÓLO BORRA LO QUE NADIE MUESTRA, y sólo pasado un plazo. El recuento de
referencias es el mismo que usa el panel: todos los campos de todas las
empresas. El plazo (24 horas por defecto) protege a la imagen recién subida que
todavía está en un formulario abierto.

    python manage.py cleanup_storefront_images
    python manage.py cleanup_storefront_images --older-than-hours 72
    python manage.py cleanup_storefront_images --dry-run
"""

from django.core.management.base import BaseCommand, CommandError

from store import storefront_media


class Command(BaseCommand):
    help = 'Borra las imágenes de la tienda sin ninguna referencia y más antiguas que el plazo.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--older-than-hours', type=int, default=24,
            help='Plazo mínimo desde la subida, en horas (24 por defecto).',
        )
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Lista lo que se borraría y no borra nada.',
        )

    def handle(self, *args, older_than_hours, dry_run, **options):
        if older_than_hours < 1:
            raise CommandError('El plazo mínimo es de 1 hora.')

        candidates = storefront_media.unplaced(older_than_hours)
        if dry_run:
            for image in candidates:
                self.stdout.write(
                    f'{image.public_id}  {image.company.slug}  {image.byte_size} bytes  '
                    f'{image.created_at:%Y-%m-%d %H:%M}'
                )
            self.stdout.write(f'{len(candidates)} imagen(es) sin uso. No se borró nada (--dry-run).')
            return

        deleted = sum(1 for image in candidates if storefront_media.delete_unplaced(image))
        self.stdout.write(self.style.SUCCESS(f'{deleted} imagen(es) sin uso borrada(s).'))
