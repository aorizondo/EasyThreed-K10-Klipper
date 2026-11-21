; ================================================================
; K9 - Nivelacion UBL completa (un solo gcode)
; ----------------------------------------------------------------
; ANTES de ejecutar: DESPLIEGA el brazo de la sonda a mano.
; Al terminar: RETIRA el brazo antes de imprimir.
; (La sonda es deploy manual; el gcode no puede desplegarla.)
; ================================================================

M117 Nivelando... sonda desplegada?

G28                  ; Homing. X/Y por endstop (o crash), Z con la sonda en el centro.

; --- Sondeo de la malla ---
G29 P1               ; Sondea automaticamente TODOS los puntos alcanzables.
G29 P3               ; Rellena por extrapolacion los puntos del borde inalcanzables
                     ; (fila trasera Y99 y columna X99, por el offset de la sonda).
G29 T                ; Imprime la malla (topologia) en el terminal para verla.

; --- Guardar y activar ---
G29 S0               ; Guarda la malla en el slot 0 (EEPROM UBL).
M500                 ; Persiste ajustes en EEPROM.
M420 S1 Z10          ; Activa UBL con fade a 10mm (la correccion se desvanece a Z=10).

M117 Nivelacion lista - retira la sonda

; ================================================================
; NOTA: los bordes (Y99, X99) van por extrapolacion (P3). Para bordes
; MEDIDOS de verdad (G30 + M421) hace falta conducirlo desde OctoPrint;
; no se puede en gcode plano (requiere leer cada medida en vivo).
;
; Para MAS puntos reales: subir la rejilla a 5x5 o 7x7 en el firmware
; (config.ini: grid_max_points_x). 5x5 (impar) anade un punto en el
; CENTRO (X50 Y50) -> capta el hundimiento central.
; ================================================================
