# Brief: rediseño de Rastro Studio

Pegá este documento en Claude Design y adjuntá `studio-sample-data.json` (en esta misma
carpeta). Todos los datos son sintéticos.

## Qué es Rastro Studio

Rastro es una herramienta open source de scraping. Studio es su interfaz local: corre en la
computadora de la persona (`rastro studio`), se abre en el navegador y trabaja sobre una
carpeta. Sirve para crear un scraper desde una URL, ver los resultados, saber si cada fuente
sigue funcionando y arreglarla cuando el sitio cambia.

La promesa del producto: **un scraper que una persona con poca experiencia puede mantener
funcionando, sin datos falsos en silencio.** Cuando algo falla, Studio lo dice en palabras.

## A quién está dirigido

- **Principal:** una persona no técnica o "vibe coder". Ejemplo: alguien que arma una agenda
  de fiestas de Uruguay con 4 sitios de entradas. Quiere ver los eventos, saber de dónde salió
  cada dato y enterarse si un sitio dejó de responder. No conoce las palabras job, receta,
  selector ni dataset, y no debería necesitarlas.
- **Secundaria:** una persona técnica que ajusta paginación, seguimiento de enlaces, scroll o
  reparación. Esas opciones existen, pero viven en "Avanzado".

## El problema de hoy

Studio es una sola página larga de tarjetas con el mismo peso visual, organizada según los
conceptos internos del motor y no según lo que la persona quiere hacer:

- La primera pantalla pide un "Job file". Un usuario real dijo: «me dice que abra un Job».
  Para ver sus resultados tuvo que hacer `Agenda-Rave.json → Open → Dataset → Show records`.
- Junto a "Show records" aparecen el selector CSS, la paginación, el spider, el scroll y los
  interruptores de reparación.
- Hay jerga en las etiquetas ("Recipe memory file, enables repair (optional)").
- No hay vista de proyectos, ni estado por fuente, ni estados vacíos explicados. Solo está
  en inglés.

## Lo que hay que diseñar

### 1. Inicio: proyectos

Studio abre una carpeta y muestra los proyectos que reconoce, por nombre.

- Una tarjeta por proyecto con nombre, estado general (al día / con avisos / con fallas),
  última actualización, cantidad de registros, fuentes con problema y el botón principal
  **Ver resultados**.
- **Estado vacío** (la carpeta no tiene proyectos): explicar qué falta y ofrecer dos caminos,
  **Empezar desde una URL** y **Conectar una exportación** (JSON o SQLite compatible), con los
  formatos admitidos dichos en una línea.
- **Carpeta con una base externa no reconocida:** explicar qué se encontró y qué hacer.

### 2. Proyecto → Resultados (la pantalla más importante)

- Tabla de registros paginada (por ejemplo «Registros 1–25 de 74»), con búsqueda y filtros por
  fuente, estado (próximo / en vivo) y fecha.
- Columnas de ejemplo para la agenda: inicio, evento, lugar, precio, fuente.
- **Procedencia:** al tocar una fila o una celda se abre un panel lateral con de qué URL salió
  cada campo, cuándo se observó y con qué versión de la extracción.
- **Celdas vacías con motivo.** No es lo mismo:
  - *No publicado* (la fuente no informa el precio),
  - *Gratis* (el evento es sin costo),
  - *La fuente falló* (no se pudo consultar en la última carga).
  Cada caso necesita un tratamiento visual y un texto distintos.
- **Precios con lotes:** un evento puede tener varios lotes («Sin costo hasta la 01:00», «$160
  hasta la 01:30», tres tandas con consumición a $199, $249 y $299). El precio de la tabla
  resume y el panel muestra cada lote con disponibilidad y condiciones.
- **Foto importada:** si los datos vienen de una importación y no de una consulta web, decirlo
  («Importado el 17/11 15:20 · observado originalmente el 17/11 14:05»).
- Exportar en JSON y HTML.

### 3. Proyecto → Fuentes (cobertura)

Una fila o tarjeta por fuente con:

- Estado en palabras: *Al día*, *Acceso denegado por el sitio (robots.txt)*, *El sitio
  devolvió algo que no se puede leer*, *Sin registros nuevos*, *Cambió la estructura del
  sitio*.
- Última consulta, registros leídos, rechazados y límites alcanzados («se leyeron 20 fichas;
  límite 20»).
- Acción siguiente clara para cada estado: *Reintentar*, *Arreglar este sitio*, *Ver detalle*.
- Avisos que no impiden guardar, por ejemplo: «Se guardaron 74 filas. No se guardó la muestra
  para reparación porque `descripción` parece contener un teléfono».

### 4. Proyecto → Alertas

Lista cronológica de lo que pasó en las corridas: falla, cambio en el sitio (drift),
cuarentena, una promoción rechazada, un límite alcanzado. Cada alerta tiene una frase en
lenguaje común y una acción.

### 5. Arreglar un sitio (reparación)

Cuando un sitio cambia, Rastro propone una nueva forma de extraer y la verifica. La persona ve
una comparación **antes / después** de los valores por campo (por ejemplo, el título sigue
siendo un título y no una ciudad) y decide **Aplicar** o **Descartar**, escribiendo un motivo.
Un interruptor separado, **Aplicar arreglos automáticamente**, viene apagado y explica en una
frase qué controles se aplican si se enciende.

### 6. Nuevo scraper desde una URL (asistente)

Pasos de a una decisión por vez:

1. Pegar la URL. Studio la consulta; si la página necesita JavaScript, ofrece usar navegador.
2. Nombrar los campos en lenguaje común y pegar un ejemplo de cada uno (o «Pedir a la IA»,
   sin ejemplos).
3. Ver una vista previa de registros y corregir un campo si sale mal.
4. Elegir el nombre del proyecto y guardar. Los nombres de archivo quedan como detalle
   avanzado.

### 7. Ajustes del proyecto (avanzado)

Agrupado y plegado: siguiente página (selector y límite), seguir enlaces (profundidad, límite
de páginas, rutas permitidas y prohibidas), scroll (pasos y espera), navegador o modo
sigiloso, permisos de red por host, conectar tu app (comandos), registro de recetas
compartidas (buscar, instalar, publicar).

## Seguridad visible

Los datos vienen de sitios de terceros y pueden contener texto que intenta dar instrucciones a
una IA. Diseñar:

- Un indicador discreto en un campo marcado como «contenido que parece instrucciones», con una
  explicación en el panel de procedencia.
- Que las imágenes y los enlaces que vienen de los datos no se carguen solos: mostrar un
  marcador con la opción de cargarlos.

## Restricciones técnicas (no negociables)

El diseño se implementa a mano en el repositorio, así que tiene que poder construirse con:

- HTML, CSS y JavaScript vanilla, sin framework y sin paso de build.
- Sin CDN ni fuentes web externas: Studio funciona offline. Usar la pila de fuentes del
  sistema o una fuente que se pueda empaquetar.
- Accesible: navegable con teclado, foco visible, contraste AA, compatible con lector de
  pantalla, y el estado nunca se comunica solo por color.
- Modo claro y oscuro.
- Español e inglés, con un selector. Los textos de diseño van en español rioplatense neutro
  (voseo: «Pegá la URL»).
- Funciona desde 360 px de ancho hasta escritorio. La tabla de resultados se adapta en móvil.

## Tono visual

Herramienta profesional y tranquila, que transmita confianza en los datos: densidad media,
mucha claridad en estados y números, poco adorno. Referencias de espíritu: paneles de
operación de buenos productos de datos y observabilidad, no un sitio de marketing. Un único
color de acento. Los colores de estado (bien / aviso / falla) son consistentes en toda la app.

## Entregables

1. Pantallas: Inicio con proyectos, Inicio vacío, Resultados, panel de procedencia, Fuentes,
   Alertas, Arreglar un sitio (antes/después), asistente desde URL (los 4 pasos) y Ajustes
   avanzados.
2. Estados: carga, vacío, error de fuente, foto importada, celda vacía por cada motivo.
3. Tokens: colores (claro y oscuro), tipografía, espaciados, radios y sombras, con nombres.
4. Componentes nombrados: tarjeta de proyecto, chip de estado, fila de fuente, tabla de
   resultados, panel de procedencia, alerta, comparación antes/después, estado vacío.
5. Una versión en inglés de al menos Inicio y Resultados, para comprobar que los textos
   entran.

## Datos de ejemplo

`studio-sample-data.json` tiene un proyecto «Agenda de fiestas · Uruguay» con 4 fuentes (una
con acceso denegado y otra limitada a 20 fichas), eventos con y sin precio, uno gratis, uno con
lotes, uno en vivo, alertas y un arreglo pendiente. Usalo en todas las pantallas en lugar de
texto de relleno.
