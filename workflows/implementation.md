# Workflow: Implementación y Revisión de Código

## Objetivo
Flujo iterativo para implementar código de calidad: primero se escribe, luego se revisa, y si hay fallos, se corrige hasta que pase la revisión.

## Cuándo ejecutar este flujo
- Cuando el usuario pida implementar una funcionalidad nueva.
- Cuando el usuario pida refactorizar o modificar código existente de forma significativa.

## Pasos

### 1. Entender el contexto
Antes de escribir código:
- Listar y leer los archivos relevantes del proyecto.
- Entender la arquitectura existente (patrones, convenciones de naming, estructura de directorios).
- Identificar dependencias y posibles efectos colaterales.

### 2. Implementar (Rol: Implementer)
Escribe el código siguiendo las convenciones del proyecto:
- Mantener consistencia con el estilo existente.
- Documentar funciones y clases con docstrings.
- No romper funcionalidad existente.

### 3. Auto-Revisión (Rol: Reviewer)
Antes de presentar el código al usuario, haz una revisión interna:

#### Checklist de revisión
- [ ] **Corrección:** ¿El código hace lo que se pidió?
- [ ] **Edge cases:** ¿Se manejan valores nulos, listas vacías, errores de red?
- [ ] **Seguridad:** ¿Hay inyecciones, secretos expuestos o inputs sin validar?
- [ ] **Rendimiento:** ¿Hay bucles innecesarios, queries N+1 o cargas excesivas en memoria?
- [ ] **Legibilidad:** ¿El código es claro sin necesidad de comentarios excesivos?
- [ ] **Tests:** ¿Se necesitan tests? ¿Se han considerado los casos principales?

### 4. Iterar si es necesario
Si la auto-revisión detecta fallos:
1. Corregir los problemas encontrados.
2. Volver a pasar el checklist.
3. Repetir hasta que todo esté limpio.

### 5. Presentar al usuario
Mostrar los cambios con una explicación clara:
- Qué se cambió y por qué.
- Decisiones de diseño tomadas.
- Cualquier punto que necesite la opinión del usuario.
