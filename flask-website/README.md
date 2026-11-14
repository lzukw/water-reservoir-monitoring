## Kommandozeilen-Argumente für Flask-Server während Test mit VM

--flask-host=:: --flask-port=5000 --redis-server="deb13.local" --camera-images-folder="/home/zukw/probe/water-reservoir-monitoring/data/camera-images" --water-level-csv-file="/home/zukw/probe/water-reservoir-monitoring/data/water-level/sensor-data.csv" --water-meter-csv-file="/home/zukw/probe/water-reservoir-monitoring/data/water-meter/sensor-data.csv" 

## chart.js

chart.js wurde heruntergeladen mit

```bash
wget https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.js
mv chart.umd.js website/static/chart.umd.js
```