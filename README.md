# H3 Audio Refinement Controls (plugin WanGP)

Pour les modèles **MiniMax H3** (FL2VA, REF2VA, ControlNet-Union), ce plugin remplace la liste déroulante
**Audio Refinement Extra Phase** (`None` / `Enabled (6 extra steps, denoising 0.5)`) par :

| Contrôle | Plage | Défaut |
|---|---|---|
| ☑ **Audio Refinement Extra Phase** (case à cocher) | activé / désactivé | désactivé |
| **Refinement Extra Steps** (slider) | 1 à 6 | 6 |
| **Refinement Denoising Strength** (slider) | 0 à 1 (pas 0,01) | 0,5 |
| ☑ **Keep Audio References** (case à cocher) | activé / désactivé | désactivé |

Les sliders et la case « Keep Audio References » sont grisés quand le raffinement est décoché. Une force de débruitage de 0 désactive la phase
(rien à débruiter) ; 1 re-bruite entièrement l'audio. Avec les valeurs par défaut (6 étapes, 0,5), le
planning de débruitage est **exactement** celui de WanGP d'origine.

### Bruit réel appliqué à l'audio

WanGP applique à l'audio la courbe `3·d / (1 + 2·d)` : la valeur du slider n'est pas le bruit réel. Le texte
sous le slider l'indique (0,05 → ~14 %, 0,10 → ~25 %, 0,20 → ~43 %, 0,50 → ~75 %). Pour ne corriger que les
artefacts, 1 étape à ~0,1 est un bon point de départ (une seule étape en pleine résolution, ~+25 s en 720p).

### Keep Audio References

Sans cette option (comportement d'origine de WanGP), le raffinement ne voit que le prompt, la vidéo finale et
l'audio à corriger : aucune référence. Avec l'option, les **références audio** sont réinjectées :

* guides audio (`A`, `AB`, `ABD`), extraits de bande-son (`K1`), bande-son des vidéos de référence (`K`) ;
* RefMods audio du plugin *MiniMax H3 RefMods*, en REF2VA comme en FL2VA.

Elles reprennent leurs balises `<Audio N>` dans le prompt, dans le même ordre que pendant la génération, pour
que la voix reste rattachée à sa référence. Les références image et vidéo ne sont pas réinjectées (coûteuses en
jetons et sans apport pour l'audio). Coût : quelques centaines de jetons audio, négligeable face à la vidéo.

### Compatibilité avec MiniMax H3 RefMods

* Le plugin ne remplace jamais `generate` : il modifie seulement le code interne de la fonction, donc les patchs
  de RefMods restent en place quel que soit l'ordre de chargement.
* Corrige un plantage de RefMods en FL2VA : pendant le raffinement, RefMods ajoutait ses références sans les
  lignes de condition correspondantes (« layout mismatch »). Le plugin coupe l'injection FL2VA de RefMods pour
  la phase de raffinement uniquement (les RefMods audio restent disponibles via « Keep Audio References »).

## Installation

1. Copier le dossier `wan2gp-h3-audio-refinement` dans le dossier `plugins/` de WanGP
   (ou l'installer depuis l'onglet **Plugins** si vous le publiez sur GitHub).
2. Onglet **Plugins** → cocher le plugin → **Save Settings**.
3. **Redémarrer WanGP.** Au démarrage, la console affiche
   `[H3 Audio Refinement] active: N MiniMax H3 model definitions updated, exact denoising schedule.`

## Fonctionnement

* WanGP limite chaque modèle à 5 « custom settings » et H3 REF2VA en utilise déjà 4 : impossible d'ajouter
  trois réglages natifs. Les trois valeurs sont donc stockées dans le réglage existant `audio_refinement`
  sous la forme `enabled:<steps>:<denoising>[:audio_refs]` (ex. `enabled:1:0.1:audio_refs`) ou `disabled:6:0.5`.
  Elles sont ainsi sauvegardées avec les réglages du modèle, dans les métadonnées des vidéos, dans la file
  d'attente, et utilisables par Deepy / l'API (`custom_settings.audio_refinement`).
* Le champ texte brut est masqué dans le formulaire ; le plugin affiche à la place la case et les deux
  sliders juste après les réglages spécifiques au modèle, et les garde synchronisés (changement de modèle,
  chargement de réglages, onglet Edit de la file d'attente).
* Les anciennes valeurs `none` / `enabled` restent comprises (= désactivé / 6 étapes à 0,5).
* La visibilité d'origine est conservée : les contrôles sont masqués quand une bande-son d'entrée pilote le
  résultat (FL2VA avec `A`/`K`, REF2VA avec `S`), et absents des variantes PDD.
* `MiniMaxH3Pipeline.generate` est enveloppé pour appliquer le nombre d'étapes et la force choisis. Le
  planning de sigmas devient `linspace(denoising, 0, steps + 1)` (exact pour toute valeur). Si une future
  version de WanGP modifie ce passage du code, le plugin bascule automatiquement sur la grille d'origine de
  WanGP (force arrondie à `steps / round(steps / denoising)`) et l'indique dans la console.

## Limites

* La valeur choisie s'enregistre au relâchement du slider (ou en quittant le champ numérique).
* « Keep Audio References » a été vérifié sur le vrai code de WanGP (CPU, modèles simulés) : la disposition des
  jetons est cohérente dans tous les cas testés, mais l'effet sur la qualité est à juger à l'oreille.
* Si vous désactivez le plugin, la liste déroulante d'origine revient (sur `None` pour les réglages enregistrés
  avec le plugin). Les tâches mises en file d'attente avec le plugin doivent être exécutées avec le plugin actif.
