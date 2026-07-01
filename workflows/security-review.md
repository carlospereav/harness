# Workflow: Security Review (Pre-Commit)

## Objetivo
Realizar una auditoría de seguridad de todos los cambios pendientes en el working tree antes de hacer un commit.

## Cuándo ejecutar este flujo
- Cuando el usuario pida una revisión de seguridad.
- Cuando el usuario diga que va a hacer commit o pida preparar un commit.
- Cuando el usuario diga "revisa mis cambios" o similar.

## Pasos

### 1. Obtener los cambios
Ejecuta el siguiente comando en el directorio del proyecto:
```bash
git diff HEAD
```
Si el repositorio no tiene commits previos, usa `git diff --cached` como fallback.

### 2. Analizar el diff
Revisa **cada línea añadida** (`+`) del diff buscando los siguientes riesgos:

#### Credenciales y Secretos
- API keys, tokens, passwords hardcodeados en el código.
- Cadenas que parezcan claves (patrones como `sk-`, `ghp_`, `AKIA`, etc.).
- Archivos `.env` o de configuración con secretos que no deberían estar trackeados.

#### Inyecciones
- SQL sin parametrizar (string concatenation en queries).
- Ejecución de comandos del sistema con input del usuario sin sanitizar (`subprocess`, `os.system`, `eval`, `exec`).
- XSS: inserción de HTML/JS sin escapar.

#### Criptografía y Autenticación
- Uso de algoritmos débiles (MD5, SHA1 para passwords, DES).
- Tokens sin expiración o sin validación.
- Cookies sin flags `HttpOnly`, `Secure`, `SameSite`.

#### Exposición de Datos
- Logs que impriman información sensible (emails, passwords, tokens).
- Endpoints que expongan datos internos sin autenticación.
- CORS demasiado permisivo (`*`).

### 3. Generar el reporte
Presenta los hallazgos en este formato:

```
🔒 REPORTE DE SEGURIDAD
========================

📁 Archivo: <ruta del archivo>
⚠️ Riesgo: <ALTO | MEDIO | BAJO>
📍 Línea: <número de línea en el diff>
🔍 Hallazgo: <descripción del problema>
💡 Solución: <cómo solucionarlo>

---
(repetir por cada hallazgo)

✅ RESUMEN: X hallazgos (Y altos, Z medios, W bajos)
```

Si no hay hallazgos:
```
🔒 REPORTE DE SEGURIDAD
========================
✅ No se han detectado vulnerabilidades en los cambios actuales.
El código parece seguro para commitear.
```

### 4. Recomendación final
- Si hay hallazgos de riesgo **ALTO**: recomendar NO commitear hasta resolver.
- Si hay hallazgos de riesgo **MEDIO**: advertir, pero dejar al criterio del usuario.
- Si solo hay **BAJO** o nada: dar luz verde.
