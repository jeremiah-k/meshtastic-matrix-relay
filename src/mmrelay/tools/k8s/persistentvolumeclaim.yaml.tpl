apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: mmrelay-data
  namespace: {{NAMESPACE}}
  labels:
    app: mmrelay
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: {{STORAGE_CLASS}}
  resources:
    requests:
      storage: {{STORAGE_SIZE}}
