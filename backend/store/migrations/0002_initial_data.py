from django.db import migrations


def create_initial_data(apps, schema_editor):
    Category = apps.get_model('store', 'Category')
    Product = apps.get_model('store', 'Product')

    phones = Category.objects.create(name='iPhone', slug='iphone')
    accessories = Category.objects.create(name='Accesorios', slug='accesorios')

    Product.objects.bulk_create([
        Product(name='iPhone 15 Pro', slug='iphone-15-pro', description='El smartphone más avanzado de Apple.', price='5599.00', inventory=10, category=phones),
        Product(name='Apple Watch Series 9', slug='apple-watch-series-9', description='Reloj inteligente con health tracking.', price='2499.00', inventory=15, category=accessories),
        Product(name='AirPods Pro (2.ª gen)', slug='airpods-pro-2', description='Cancelación de ruido activa y audio espacial.', price='999.00', inventory=20, category=accessories),
    ])


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(create_initial_data),
    ]
