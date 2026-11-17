"""
Catalogue constants shared by several readers. This module imports NOTHING from `store`, so any module may
import it at load time without risking a circular import.
"""

#: A1b — the names an attribute may have to be read as the capacity when the company assigned no capacity ROLE.
#: The pasted list (`catalog_proposal`), the supplier assistant (`configuration_resolver.Vocabulary.roles_of`)
#: and «Preparación del catálogo» (`catalog_readiness`) take it from here, case-insensitively.
CAPACITY_NAME = r'^(capacidad|almacenamiento|capacity|storage)$'
