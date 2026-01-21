apiVersion: apps/v1
kind: Deployment
metadata:
  name: mmrelay
  namespace: {{NAMESPACE}}
  labels:
    app: mmrelay
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mmrelay
  template:
    metadata:
      labels:
        app: mmrelay
    spec:
      containers:
        - name: mmrelay
          image: ghcr.io/jeremiah-k/mmrelay:{{IMAGE_TAG}}
          imagePullPolicy: Always

          # Command override to use mounted config and data directory
          command: ["mmrelay"]
          args:
            - "--config"
            - "/app/config.yaml"
            - "--data-dir"
            - "/app/data"
            - "--logfile"
            - "/app/data/logs/mmrelay.log"

          env:
            - name: TZ
              value: "UTC"
            - name: PYTHONUNBUFFERED
              value: "1"
            - name: MPLCONFIGDIR
              value: "/tmp/matplotlib"

          # Matrix credentials from Kubernetes Secret (for env var authentication)
          # If not using credentials.json, create the secret with:
          #   kubectl create secret generic mmrelay-matrix-credentials \
          #     --from-literal=MMRELAY_MATRIX_HOMESERVER=https://matrix.example.org \
          #     --from-literal=MMRELAY_MATRIX_BOT_USER_ID=@bot:matrix.example.org \
          #     --from-literal=MMRELAY_MATRIX_PASSWORD=your_password
          envFrom:
            - secretRef:
                name: mmrelay-matrix-credentials
                optional: true

          volumeMounts:
            # Mount config from ConfigMap
            - name: config
              mountPath: /app/config.yaml
              subPath: config.yaml
              readOnly: true

            # Mount persistent data directory for database, logs, E2EE store
            - name: data
              mountPath: /app/data

            {{CREDENTIALS_VOLUME_MOUNT}}

            {{SERIAL_VOLUME_MOUNT}}

          resources:
            requests:
              memory: "256Mi"
              cpu: "100m"
            limits:
              memory: "512Mi"
              cpu: "500m"

          # Health check using process monitoring
          # NOTE: pgrep is a basic process check. For production, consider implementing
          # a dedicated health check endpoint that verifies connectivity to Matrix and Meshtastic.
          livenessProbe:
            exec:
              command:
                - pgrep
                - -f
                - mmrelay
            initialDelaySeconds: 30
            periodSeconds: 30
            timeoutSeconds: 10
            failureThreshold: 3

          readinessProbe:
            exec:
              command:
                - pgrep
                - -f
                - mmrelay
            initialDelaySeconds: 10
            periodSeconds: 10
            timeoutSeconds: 5

      volumes:
        # ConfigMap volume for config.yaml
        - name: config
          configMap:
            name: mmrelay-config
            items:
              - key: config.yaml
                path: config.yaml

        # Persistent volume for data (database, logs, E2EE store)
        - name: data
          persistentVolumeClaim:
            claimName: mmrelay-data

        {{CREDENTIALS_VOLUME}}

        {{SERIAL_VOLUME}}

      # For serial/BLE connections, you may need specific node selection
      # nodeSelector:
      #   kubernetes.io/hostname: specific-node-with-device

      {{SECURITY_CONTEXT}}

      restartPolicy: Always
