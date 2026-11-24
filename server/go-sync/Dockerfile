# go-sync di Brave (MPL-2.0) con le modifiche di Shannon in patches/.
# Si costruisce sul proprio server: scarica il sorgente di go-sync a un commit
# fisso, applica le patch, ne esegue i test e aggiunge crea-tabella.
FROM golang:1-alpine3.24 AS build
ARG GO_SYNC_COMMIT=76745a639255f81c5a4a3d5093d6a3f0fa53f50e
WORKDIR /src
RUN wget -qO- "https://github.com/brave/go-sync/archive/${GO_SYNC_COMMIT}.tar.gz" \
    | tar -xz --strip-components=1
COPY patches/ /patches/
RUN apk add --no-cache patch \
 && for p in /patches/*.patch; do patch -p1 < "$p" || exit 1; done
COPY crea-tabella/ ./cmd/crea-tabella/
RUN go mod download
RUN CGO_ENABLED=0 go test ./auth/
RUN CGO_ENABLED=0 go build -trimpath -o /out/go-sync . \
 && CGO_ENABLED=0 go build -trimpath -o /out/crea-tabella ./cmd/crea-tabella

FROM alpine:3.24
COPY --from=build /out/ /usr/local/bin/
COPY --from=build /src/schema/dynamodb/table.json /etc/go-sync/table.json
USER 65534:65534
EXPOSE 8295
CMD ["go-sync"]
