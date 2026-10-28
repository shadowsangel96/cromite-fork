# overlay/

File copiati così come sono sopra i sorgenti, dopo le patch (stesso percorso relativo a
`src/`). Servono per i file binari, per esempio le icone del browser:

    overlay/chrome/app/theme/chromium/product_logo_256.png

Le modifiche al codice vanno invece in `patches/`: un file in overlay sostituisce del
tutto quello di Chromium e non si fonde con i cambiamenti upstream.
