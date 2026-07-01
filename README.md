# Harness — Protocolos de Desarrollo para Antigravity

Este repositorio contiene los workflows (protocolos) que Antigravity debe seguir al trabajar en tus proyectos de desarrollo.

## Cómo usar

### Opción 1: Referencia desde GEMINI.md (Recomendado)
En el `GEMINI.md` de cualquiera de tus proyectos, añade una referencia a los workflows que quieras activar:

```markdown
## Workflows

Sigue los protocolos definidos en:
- [Revisión de Seguridad](../harness/workflows/security-review.md)
- [Implementación y Revisión](../harness/workflows/implementation.md)
```

> **Nota:** Esto asume que `harness` está al mismo nivel que tus otros repos en `C:\Users\carlo\GitHub\`.

### Opción 2: Copiar los workflows
Copia los archivos de `workflows/` directamente al directorio `.gemini/` o a la raíz de tu proyecto.

## Workflows disponibles

| Workflow | Archivo | Descripción |
|---|---|---|
| **Security Review** | `workflows/security-review.md` | Auditoría de seguridad del git diff antes de commitear |
| **Implementation** | `workflows/implementation.md` | Flujo iterativo de implementación + auto-revisión |

## Filosofía

El objetivo de este repositorio es ir construyendo progresivamente un sistema de protocolos donde puedas delegar cada vez más tareas de programación y gestión de contexto a Antigravity, manteniendo la calidad y la seguridad mediante flujos de revisión estructurados.