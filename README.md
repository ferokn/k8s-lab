# Lokálny Kubernetes lab – frontend + backend, RBAC, NetworkPolicy, GitOps, CI/CD, Monitoring

Tento návod ťa prevedie **od úplnej nuly** (žiadny účet, žiadny nástroj nainštalovaný) až po
bežiaci lokálny "produkčný" cluster na tvojom Ubuntu s appkou, bezpečnosťou, GitOps a monitoringom.

Pri každom kroku je najprv vysvetlené **čo a prečo** robíme, až potom príkazy. Cieľ nie je len
"skopírovať a spustiť", ale pochopiť princíp, ktorý sa v reálnej praxi (aj u teba na OpenShifte)
používa rovnako.

---

## 0. Prehľad princípov (najprv pochop, potom rob)

| Vrstva | Nástroj | Princíp |
|---|---|---|
| Beh appky | Deployment + Service | Deployment drží požadovaný počet replík podov nažive (self-healing, rolling update). Service dáva podom stabilnú IP/DNS meno, lebo pody samotné sú efemérne. |
| Vstup zvonka | Ingress + Ingress Controller | Service typu ClusterIP nie je dostupný mimo clustra. Ingress je L7 pravidlo ("host apps.local, path /api → backend"), ktoré vykonáva Ingress Controller (nginx pod, ktorý reálne počúva na porte 80/443). |
| Konfigurácia | ConfigMap + Secret | Rovnaký image, iné prostredie = iná konfigurácia zvonka, nie zabudovaná v image. Secret je oddelený, lebo sa dá inak zabezpečiť (RBAC, šifrovanie, mount ako tmpfs). |
| Prístupové práva | RBAC (ServiceAccount, Role, RoleBinding) | Princíp najmenších oprávnení – appka smie v API serveri robiť presne to, čo potrebuje, a nič viac. |
| Sieťová izolácia | NetworkPolicy | Kubernetes bez NetworkPolicy = plochá sieť, každý pod vidí každého. NetworkPolicy je "firewall" na úrovni podov/namespace. |
| Deployment automation | GitOps (ArgoCD) | Git = jediný zdroj pravdy o stave clustra. Namiesto `kubectl apply` z laptopu cluster sám sleduje Git a synchronizuje sa (pull model, auditovateľné, rollback = `git revert`). |
| Build & release | CI/CD (GitHub Actions) | Automatizácia cesty "zmenil som kód → vznikol nový image → cluster beží na novom image". CI (build) je oddelené od CD (nasadenie), CD robí GitOps vrstva. |
| Pozorovateľnosť | Prometheus + Alertmanager | Prometheus pravidelne "sťahuje" (scrape) metriky. PrometheusRule definuje, kedy je stav zlý. Alertmanager rozhoduje, komu a ako to poslať. |

Ak toto dáva zmysel, poďme na to prakticky.

---

## 1. Architektúra

```
                         ┌────────────────────┐
   Internet/localhost    │   ingress-nginx     │
   apps.local  ─────────▶│   Ingress Controller │
                         └──────────┬──────────┘
                     /            │            \api
                    ▼                          ▼
           ┌─────────────┐            ┌─────────────┐
           │  frontend    │            │   backend    │
           │  (nginx)     │  fetch     │  (Flask)     │
           │  Deployment  │───/api───▶ │  Deployment  │
           │  Service     │            │  Service     │
           └─────────────┘            └──────┬──────┘
                                              │ envFrom
                                    ┌─────────┴─────────┐
                                    │ ConfigMap + Secret │
                                    └────────────────────┘

  RBAC:  backend beží pod ServiceAccount webapp-sa s Role (get/list/watch pods, configmaps)
  NetworkPolicy: default-deny + explicitné povolenia (ingress→frontend, frontend→backend, monitoring→backend)
  GitOps: ArgoCD sleduje GitHub repo, automaticky synchronizuje Helm chart do clustra
  CI/CD: GitHub Actions – build image → push do GHCR → bump tagu v Helm values.yaml → ArgoCD sync
  Monitoring: kube-prometheus-stack (Prometheus + Alertmanager + Grafana), ServiceMonitor, PrometheusRule alerty
```

Namespace pre appku: `webapp`. Ingress host: `apps.local`.

---

## 2. Predpoklady (Ubuntu) – inštalácia nástrojov

Prečo presne tieto nástroje:
- **git** – bez neho niet GitOps ani CI/CD, všetko stojí na verziovanom repozitári.
- **Docker** – kind spúšťa Kubernetes uzly ako Docker kontajnery, potrebuješ Docker daemon.
- **kubectl** – CLI klient, ktorým hovoríš s Kubernetes API serverom.
- **kind** – vyrobí ti skutočný (malý) Kubernetes cluster lokálne, zadarmo, bez cloudu.
- **helm** – balíčkovač K8s manifestov (šablóny + hodnoty), nasadíš ním appku aj monitoring.
- **gh** (GitHub CLI) – najjednoduchší spôsob, ako z terminálu vyrobiť GitHub repo a autentifikovať sa.
- **yq** – úprava YAML z príkazového riadku, použije to CI/CD pipeline.

```bash
sudo apt update

# git
sudo apt install -y git

# Docker
sudo apt install -y docker.io
sudo usermod -aG docker $USER && newgrp docker

# kubectl
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl

# kind
curl -Lo kind https://kind.sigs.k8s.io/dl/latest/kind-linux-amd64
chmod +x kind && sudo mv kind /usr/local/bin/

# helm
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

# GitHub CLI
sudo apt install -y gh

# yq
sudo snap install yq
```

Over, že všetko funguje: `docker version`, `kubectl version --client`, `kind version`, `helm version`, `gh --version`.

---

## 3. Krok 0: Git a GitHub od nuly

Toto je vrstva, ktorá v pôvodnom zadaní chýbala – GitOps a CI/CD potrebujú **existujúci Git
repozitár**, tak si ho teraz spolu vyrobíme.

### 3.1 Založ si GitHub účet
Ak ho nemáš: choď na https://github.com/signup, zaregistruj sa (email, heslo, meno).
Toto je jediný krok, ktorý za teba nič neurobí – treba ho spraviť ručne v prehliadači.

### 3.2 Prihlás sa cez GitHub CLI z terminálu
`gh` ti vybaví autentifikáciu (vytvorí a uloží token) bez toho, aby si ručne chodil do
Settings → Developer settings → Personal Access Tokens.

```bash
gh auth login
# Zvoľ: GitHub.com → HTTPS → Login with a web browser
# Otvorí sa kód, ktorý zadáš na github.com/login/device
```

### 3.3 Priprav si lokálny priečinok s projektom
Rozbaľ `k8s-lab.zip` (dostal si ho v predchádzajúcej správe) do vlastného priečinka:

```bash
cd ~
unzip k8s-lab.zip
cd k8s-lab
git init
git add .
git commit -m "init: kubernetes lab - frontend, backend, helm, gitops, cicd, monitoring"
```

### 3.4 Vytvor GitHub repozitár a pushni doň

```bash
gh repo create k8s-lab --private --source=. --remote=origin --push
```

Tento jeden príkaz spraví 3 veci naraz: vytvorí repo `k8s-lab` na tvojom GitHub účte, nastaví
ho ako `origin` a rovno tam pushne tvoj `main` branch.

```bash
gh repo view --web   # over si v prehliadači, že repo a súbory sú tam
gh api user --jq .login   # zisti si svoje GitHub meno, budeš ho potrebovať nižšie
```

### 3.5 Uprav referencie na repo v projekte

V `gitops/argocd-application.yaml` treba `<tvoj-github-username>` nahradiť skutočným menom:

```bash
sed -i "s#<tvoj-github-username>#$(gh api user --jq .login)#" gitops/argocd-application.yaml
git add gitops/argocd-application.yaml
git commit -m "chore: nastavenie repoURL pre ArgoCD"
git push
```

Odteraz máš skutočný Git repozitár, na ktorom stojí zvyšok labu.

---

## 4. Vytvorenie lokálneho clustra (kind) s ingress-nginx

**Prečo kind:** vyrobí multi-node-schopný Kubernetes cluster ako sadu Docker kontajnerov –
najbližšie k reálnemu správaniu clustra oproti jednoduchším simuláciám.
**Prečo `extraPortMappings`:** kind cluster beží izolovaný vo vnútri Dockeru. Namapovaním portu
80/443 z kontajnera na localhost hostiteľa vieš appku volať ako `curl http://apps.local/...`
priamo z Ubuntu bez port-forwardu.

```bash
cat <<'EOF' > kind-config.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    kubeadmConfigPatches:
      - |
        kind: InitConfiguration
        nodeRegistration:
          kubeletExtraArgs:
            node-labels: "ingress-ready=true"
    extraPortMappings:
      - containerPort: 80
        hostPort: 80
        protocol: TCP
      - containerPort: 443
        hostPort: 443
        protocol: TCP
EOF

kind create cluster --name lab --config kind-config.yaml
kubectl cluster-info   # over, že kubectl "vidí" nový cluster
```

**Prečo treba Ingress Controller:** samotný `Ingress` objekt je len konfigurácia (pravidlo).
Niekto ho musí reálne vykonávať – to je úloha `ingress-nginx` (beží ako pod, ktorý naozaj
počúva na porte 80/443 a smeruje prevádzku podľa Ingress pravidiel).

```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
kubectl wait --namespace ingress-nginx \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller \
  --timeout=120s
```

NetworkPolicy nižšie odkazuje na namespace `ingress-nginx` cez label. Kubernetes od verzie 1.21
automaticky pridáva `kubernetes.io/metadata.name` label na každý namespace – over si to:

```bash
kubectl get ns ingress-nginx --show-labels
```

---

## 5. Build a nahratie image do kind clustra

**Princíp:** kind cluster nemá prístup k Docker images na hostiteľovi cez bežný
`imagePullPolicy` – nemá vlastný registry a nevidí lokálny Docker daemon ako zdroj images.
`kind load docker-image` je "skratka", ktorá natiahne image priamo do containerd na uzloch
clustra. (V reálnom clustri by si namiesto toho pushol image do skutočného registry.)

```bash
docker build -t webapp-backend:latest apps/backend
docker build -t webapp-frontend:latest apps/frontend

kind load docker-image webapp-backend:latest --name lab
kind load docker-image webapp-frontend:latest --name lab
```

---

## 6. Nasadenie aplikácie cez Helm

**Prečo Helm a nie "surové" YAML:** Helm chart je šablóna + `values.yaml` s parametrami.
Rovnaký chart vieš nasadiť do dev/test/prod len zmenou `values.yaml` (iný namespace, iný počet
replík, iný image tag) – presne to isté očakávaš aj na OpenShift clustroch v práci.

```bash
helm install webapp helm-chart/webapp
# pri zmene v hodnotách/šablónach:
helm upgrade webapp helm-chart/webapp

kubectl get all -n webapp
kubectl get networkpolicy -n webapp
kubectl get role,rolebinding,sa -n webapp
```

Ingress smeruje na host `apps.local` – keďže nemáš skutočný DNS, namapuj si ho na localhost
(kam kind presmeroval port 80):

```bash
echo "127.0.0.1 apps.local" | sudo tee -a /etc/hosts
```

Test – frontend zavolá backend cez `/api`, presne podľa Ingress pravidla:

```bash
curl http://apps.local/api/hello
curl -I http://apps.local/
# alebo otvor http://apps.local v prehliadači
```

### Over si bezpečnosť (nielen že appka funguje, ale že sú aj hranice)

**RBAC test** – ServiceAccount appky smie čítať, ale nič meniť/mazať:

```bash
kubectl auth can-i get pods --as=system:serviceaccount:webapp:webapp-sa -n webapp        # yes
kubectl auth can-i delete pods --as=system:serviceaccount:webapp:webapp-sa -n webapp     # no
```

**NetworkPolicy test** – pod z iného namespace nemá k backendu prístup, lebo `default-deny-ingress`
zakazuje všetko, čo nie je explicitne povolené (frontend→backend, ingress-nginx→frontend):

```bash
kubectl run tmp-test --rm -it --image=busybox --restart=Never -n default -- \
  wget -qO- --timeout=3 http://backend.webapp.svc.cluster.local:8080/api/health
# očakávaný výsledok: timeout / zlyhanie spojenia
```

---

## 7. GitOps – ArgoCD

**Princíp (dôležité pochopiť):** doteraz si appku nasadzoval "push" spôsobom – ty si z laptopu
spustil `helm install`. GitOps to obracia na "pull" model: v clustri beží ArgoCD, ktorý si sám
periodicky ťahá stav z Git repozitára a porovnáva ho so skutočným stavom clustra. Ak sa líšia,
ArgoCD to opraví (sync). Výhody: Git = jediná pravda, plný audit log, jednoduchý rollback
(`git revert`), a nikto nepotrebuje priamy `kubectl` prístup do produkcie.

```bash
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl -n argocd wait --for=condition=available deployment --all --timeout=180s
```

Prístup do UI (v samostatnom termináli, nechaj bežať):

```bash
kubectl -n argocd port-forward svc/argocd-server 8080:443
```

Prihlasovacie heslo pre `admin`:

```bash
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d
```

Otvor https://localhost:8080 (certifikát je self-signed, prehliadač si to vyžiada potvrdiť).

Teraz zaregistruj appku do ArgoCD – **toto je bod, kedy ArgoCD prevezme zodpovednosť za
synchronizáciu clustra s tvojím GitHub repom** (repoURL sme nastavili v kroku 3.5):

```bash
kubectl apply -f gitops/argocd-application.yaml
```

Over v UI (Applications → webapp), že status je `Synced` a `Healthy`.

**Over si princíp "self-heal" naživo:**

```bash
kubectl scale deployment backend -n webapp --replicas=5
kubectl get deployment backend -n webapp -w
# ArgoCD do pár desiatok sekúnd vráti počet replík späť na hodnotu z Git (2) - "selfHeal: true"
```

---

## 8. CI/CD – GitHub Actions

**Princíp:** CI/CD je "posledná míľa" pred GitOps. Vývojár zmení kód appky → pipeline automaticky
zbuildí nový image, pushne ho do registry a **zapíše nový tag do Helm `values.yaml` v Git
repozitári**. Práve tento zápis do Git je moment odovzdania zodpovednosti GitOps vrstve – CI/CD
sám nikdy priamo nenasadzuje do clustra, iba mení Git.

```bash
mkdir -p .github/workflows
cp cicd/ci-cd.yaml .github/workflows/ci-cd.yaml
git add .github/workflows/ci-cd.yaml
git commit -m "ci: pridanie CI/CD pipeline"
git push
```

Over v prehliadači (`gh repo view --web` → záložka *Actions*), že workflow existuje.

Vyskúšaj celý kolobeh naživo – uprav niečo v appke a pushni:

```bash
sed -i 's/Hello from backend/Hello from backend v2/' apps/backend/app.py
git add apps/backend/app.py
git commit -m "feat: zmena hlasky v backend api"
git push
```

Čo sa stane (sleduj v GitHub Actions tabe):
1. Job `build-and-push` zbuilduje nový `webapp-backend` image a pushne ho do GHCR (`ghcr.io/<tvoje meno>/webapp-backend`).
2. Job `update-helm-chart` upraví `helm-chart/webapp/values.yaml` (nový image tag = git short SHA) a pushne to späť na `main`.
3. Tento push je zmena v Git, ktorú o pár sekúnd zachytí ArgoCD (krok 7) a sám nasadí nový image do clustra.

> Keďže image je teraz v GHCR (nie lokálne v kind), nastav ho ako **verejný** v GitHub Package
> settings (Package → Package settings → Change visibility → Public), inak ho lokálny kind
> cluster nebude vedieť stiahnuť bez `imagePullSecret`. Pre lab je toto najjednoduchšia cesta.

---

## 9. Monitoring – Prometheus + Alertmanager

**Princíp:** Prometheus pravidelne (každých 15s podľa `ServiceMonitor`) sťahuje metriky z
`/metrics` endpointu backendu (pridali sme `prometheus_client` priamo do Flask appky).
`PrometheusRule` definuje podmienky, kedy je niečo v poriadku/zle (napr. cieľ je nedostupný
2 minúty). Keď sa alert spustí, ide do Alertmanagera, ktorý rozhoduje o routovaní/deduplikácii
(v labe necháme prázdny receiver – v reálnej prevádzke by tu bol Slack/email/PagerDuty webhook).

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

kubectl create namespace monitoring
helm install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  -n monitoring -f monitoring/prometheus-values.yaml

kubectl apply -f monitoring/servicemonitor.yaml
kubectl apply -f monitoring/prometheusrules.yaml
```

Prístup do UI (každý v samostatnom termináli):

```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090
kubectl -n monitoring port-forward svc/kube-prometheus-stack-alertmanager 9093:9093
kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80
```

- Prometheus http://localhost:9090 → Status → Targets: over, že `webapp-backend` je `UP`.
- Alertmanager http://localhost:9093: zoznam aktívnych alertov.
- Grafana http://localhost:3000 (`admin` / `admin`).

Vyskúšaj alert naživo (zmaž backend a sleduj, ako sa `BackendDown` po 2 minútach spustí):

```bash
kubectl scale deployment backend -n webapp --replicas=0
# počkaj 2 min, sleduj http://localhost:9093
kubectl scale deployment backend -n webapp --replicas=2   # vráť späť
```

Definované alerty (`monitoring/prometheusrules.yaml`):
- `BackendDown` – backend target je down > 2 min,
- `BackendPodCrashLooping` – viac ako 3 reštarty pódu za 15 min,
- `BackendHighMemoryUsage` – backend nad 85 % memory limitu.

---

## 10. Upratanie

```bash
kind delete cluster --name lab
# GitHub repo (voliteľné, ak chceš celkom zmazať stopy):
gh repo delete k8s-lab --yes
```

---

## Poznámky / zjednodušenia oproti reálnej produkcii

- Secret je v `values.yaml` ako plaintext – v reálnom prostredí použi Sealed Secrets, SOPS
  alebo externý secret manager (Vault, cloud KMS).
- Bez perzistentného storage pre Prometheus (lab beží efemérne, dáta sa strácajú pri reštarte).
- Bez TLS na Ingress – pre produkciu doplniť cert-manager + Let's Encrypt / interná CA.
- Jeden `Role`/`RoleBinding` pre demonštráciu princípu least privilege; v reálnom nasadení by
  frontend a backend mali samostatné SA s ešte užšími právomocami.
- GitHub repo je `--private`; GitHub Actions naň má prístup automaticky cez `GITHUB_TOKEN`.
  ArgoCD si repo klonuje cez verejne dostupné HTTPS URL formou anonymného čítania – ak by
  repo malo zostať prísne súkromné aj pre ArgoCD, treba mu v UI (Settings → Repositories)
  pridať prístupové údaje (PAT).
