// Retargeting d'animations Mixamo (GLB) vers le humanoid d'un VRM three-vrm 2.x
// Principe : deltas de rotation en WORLD space (source animée / source repos),
// reconvertis en rotations locales sur les os *normalisés* du VRM (repos = identité).
import * as THREE from 'three';

// nom d'os mixamo (sans préfixe, minuscules) -> os humanoid VRM
const BONE_MAP = {
  hips:'hips', spine:'spine', spine1:'chest', spine2:'upperChest', neck:'neck', head:'head',
  leftshoulder:'leftShoulder', leftarm:'leftUpperArm', leftforearm:'leftLowerArm', lefthand:'leftHand',
  rightshoulder:'rightShoulder', rightarm:'rightUpperArm', rightforearm:'rightLowerArm', righthand:'rightHand',
  leftupleg:'leftUpperLeg', leftleg:'leftLowerLeg', leftfoot:'leftFoot', lefttoebase:'leftToes',
  rightupleg:'rightUpperLeg', rightleg:'rightLowerLeg', rightfoot:'rightFoot', righttoebase:'rightToes',
  lefthandthumb1:'leftThumbProximal', lefthandthumb2:'leftThumbIntermediate', lefthandthumb3:'leftThumbDistal',
  lefthandindex1:'leftIndexProximal', lefthandindex2:'leftIndexIntermediate', lefthandindex3:'leftIndexDistal',
  lefthandmiddle1:'leftMiddleProximal', lefthandmiddle2:'leftMiddleIntermediate', lefthandmiddle3:'leftMiddleDistal',
  lefthandring1:'leftRingProximal', lefthandring2:'leftRingIntermediate', lefthandring3:'leftRingDistal',
  lefthandpinky1:'leftLittleProximal', lefthandpinky2:'leftLittleIntermediate', lefthandpinky3:'leftLittleDistal',
  righthandthumb1:'rightThumbProximal', righthandthumb2:'rightThumbIntermediate', righthandthumb3:'rightThumbDistal',
  righthandindex1:'rightIndexProximal', righthandindex2:'rightIndexIntermediate', righthandindex3:'rightIndexDistal',
  righthandmiddle1:'rightMiddleProximal', righthandmiddle2:'rightMiddleIntermediate', righthandmiddle3:'rightMiddleDistal',
  righthandring1:'rightRingProximal', righthandring2:'rightRingIntermediate', righthandring3:'rightRingDistal',
  righthandpinky1:'rightLittleProximal', righthandpinky2:'rightLittleIntermediate', righthandpinky3:'rightLittleDistal',
};

// ordre hiérarchique VRM (parents avant enfants) + parent de chaque os
const ORDER = ['hips','spine','chest','upperChest','neck','head',
  'leftShoulder','leftUpperArm','leftLowerArm','leftHand',
  'leftThumbProximal','leftThumbIntermediate','leftThumbDistal',
  'leftIndexProximal','leftIndexIntermediate','leftIndexDistal',
  'leftMiddleProximal','leftMiddleIntermediate','leftMiddleDistal',
  'leftRingProximal','leftRingIntermediate','leftRingDistal',
  'leftLittleProximal','leftLittleIntermediate','leftLittleDistal',
  'rightShoulder','rightUpperArm','rightLowerArm','rightHand',
  'rightThumbProximal','rightThumbIntermediate','rightThumbDistal',
  'rightIndexProximal','rightIndexIntermediate','rightIndexDistal',
  'rightMiddleProximal','rightMiddleIntermediate','rightMiddleDistal',
  'rightRingProximal','rightRingIntermediate','rightRingDistal',
  'rightLittleProximal','rightLittleIntermediate','rightLittleDistal',
  'leftUpperLeg','leftLowerLeg','leftFoot','leftToes',
  'rightUpperLeg','rightLowerLeg','rightFoot','rightToes'];

const PARENT = (() => {
  const p = {};
  const set = (kids, parent) => kids.forEach(k => p[k] = parent);
  set(['spine'], 'hips');
  set(['chest'], 'spine');
  set(['upperChest'], 'chest');
  set(['neck'], 'upperChest');
  set(['head'], 'neck');
  for (const side of ['left', 'right']) {
    set([side + 'Shoulder'], 'upperChest');
    set([side + 'UpperArm'], side + 'Shoulder');
    set([side + 'LowerArm'], side + 'UpperArm');
    set([side + 'Hand'], side + 'LowerArm');
    const fing = ['Thumb', 'Index', 'Middle', 'Ring', 'Little'];
    const segs = { Thumb: ['Proximal', 'Intermediate', 'Distal'], Index: ['Proximal', 'Intermediate', 'Distal'] };
    for (const f of fing) {
      const s = segs[f] || ['Proximal', 'Intermediate', 'Distal'];
      set([side + f + s[0]], side + 'Hand');
      set([side + f + s[1]], side + f + s[0]);
      set([side + f + s[2]], side + f + s[1]);
    }
    set([side + 'UpperLeg'], 'hips');
    set([side + 'LowerLeg'], side + 'UpperLeg');
    set([side + 'Foot'], side + 'LowerLeg');
    set([side + 'Toes'], side + 'Foot');
  }
  return p;
})();

const norm = s => s.replace(/^mixamorig[:_]?/i, '').replace(/[\s:_.-]/g, '').toLowerCase();
const IDENT = new THREE.Quaternion();
const isFinger = n => /Thumb|Index|Middle|Ring|Little/.test(n);
const _eul = new THREE.Euler();
const LEG_UPPER = /^(left|right)UpperLeg$/;
const LEG_LOWER = /^(left|right)LowerLeg$/;
const LEG_FOOT  = /^(left|right)Foot$/;
const _q3 = new THREE.Quaternion();
const AXIS_Z = new THREE.Vector3(0, 0, 1);

export class MixamoRetarget {
  constructor() {
    this.sources = new Map();   // url -> { scene, mixer, actions, restInv, restHips, height }
    this.active = null;         // source courante
    this.action = null;
    this.vrm = null;
    this.nodes = {};            // os normalisés VRM
    this.deltaWorld = {};       // delta world par os VRM (frame courante)
    this.yaw = 0;               // correction d'orientation (radians)
    this.speed = 1;
    this.scaleY = 1;
    this._q1 = new THREE.Quaternion();
    this._q2 = new THREE.Quaternion();
    this._v1 = new THREE.Vector3();
    this._v2 = new THREE.Vector3();
    this.frameYaw = 0;   // orientation visuelle du corps induite par l'anim (rad)
    this.fingers = false;    // les doigts Mixamo ont un roulis incompatible -> on les laisse au repos
    this.legAbduct = 0.15;   // amortit l'écartement latéral des cuisses (0 = pieds joints)
    this.legTwist = 0.6;
    this.footYaw = 0.45;     // orteils moins tournés vers l'extérieur
    this.shoulders = 0.3;    // les clavicules du repos Mixamo rentrent dans le torse
    this.armWiden = 5;       // degrés d'ouverture des bras (épaules plus larges)
    this._yawQ = new THREE.Quaternion();
    this._yawInv = new THREE.Quaternion();
    this._ident = new THREE.Quaternion();
  }

  attachVRM(vrm, vrmHeight) {
    this.vrm = vrm;
    this.vrmHeight = vrmHeight;
    this.nodes = {};
    this.deltaWorld = {};
    for (const n of ORDER) {
      try { this.nodes[n] = vrm.humanoid.getNormalizedBoneNode(n) || null; } catch (e) { this.nodes[n] = null; }
      this.deltaWorld[n] = new THREE.Quaternion();   // préalloué (zéro allocation par frame)
    }
    this._hasDelta = {};
    // position de repos du bassin en espace normalisé (ex. ~0,803 m) :
    // il faut la CONSERVER et y ajouter le rebond, sinon tout le corps descend
    const h = this.nodes['hips'];
    this.hipsRestPos = h ? h.position.clone() : new THREE.Vector3(0, 0.8, 0);

    // orientation de repos du modèle, en espace os (pied -> orteils) : sert à
    // calculer automatiquement le yaw de correction, quel que soit le VRM.
    this.vrmForward = null;
    try {
      const f = vrm.humanoid.getNormalizedBoneNode('leftFoot') || vrm.humanoid.getNormalizedBoneNode('rightFoot');
      const t = vrm.humanoid.getNormalizedBoneNode('leftToes') || vrm.humanoid.getNormalizedBoneNode('rightToes');
      if (f && t) {
        const a = vrm.scene.worldToLocal(f.getWorldPosition(new THREE.Vector3()));
        const b = vrm.scene.worldToLocal(t.getWorldPosition(new THREE.Vector3()));
        if (Math.hypot(b.x - a.x, b.z - a.z) > 1e-4) this.vrmForward = Math.atan2(b.x - a.x, b.z - a.z);
      }
    } catch (e) { this.vrmForward = null; }
  }

  async load(loader, url) {
    const gltf = await loader.loadAsync(url);
    const scene = gltf.scene;
    scene.updateMatrixWorld(true);

    // os source par nom normalisé
    const bones = {};
    scene.traverse(o => { if (o.isBone || o.type === 'Object3D') { const k = norm(o.name); if (k) bones[k] = o; } });
    // (les os peuvent être des Bone ou des Object3D selon l'export)
    scene.traverse(o => { const k = norm(o.name); if (k) bones[k] = o; });

    const pairs = {};
    const restInv = {};
    for (const [k, vrmName] of Object.entries(BONE_MAP)) {
      const src = bones[k];
      if (!src) continue;
      pairs[vrmName] = src;
      const q = new THREE.Quaternion();
      src.getWorldQuaternion(q);
      restInv[vrmName] = q.clone().invert();
    }

    // direction "avant" du rig source, mesurée en pose de repos (pied -> orteils)
    let forward = { x: 0, z: 1 };
    {
      const f = pairs['leftFoot'] || pairs['rightFoot'];
      const t = pairs['leftToes'] || pairs['rightToes'];
      if (f && t) {
        const a = f.getWorldPosition(new THREE.Vector3());
        const b = t.getWorldPosition(new THREE.Vector3());
        const dx = b.x - a.x, dz = b.z - a.z;
        if (Math.hypot(dx, dz) > 1e-4) forward = { x: dx, z: dz };
      }
    }

    const hips = bones['hips'];
    const restHips = new THREE.Vector3();
    if (hips) hips.getWorldPosition(restHips);

    const box = new THREE.Box3().setFromObject(scene);
    const height = Math.max(0.001, box.getSize(new THREE.Vector3()).y);

    const mixer = new THREE.AnimationMixer(scene);
    const actions = {};
    for (const clip of gltf.animations) {
      const a = mixer.clipAction(clip);
      a.clampWhenFinished = false;
      actions[clip.name] = a;
    }

    const entry = { scene, mixer, actions, pairs, restInv, restHips, height, forward, clips: gltf.animations.map(c => c.name) };
    this.sources.set(url, entry);
    return entry;
  }

  play(url, clipName, fade = 0.3) {
    const src = this.sources.get(url);
    if (!src) return false;
    const next = src.actions[clipName];
    if (!next) return false;

    // clips très courts (< 0,5 s) = poses figées : lecture unique, puis maintien
    const dur = next.getClip().duration;
    const oneShot = dur < 0.5;

    if (this.active !== src) {
      for (const s of this.sources.values()) s.mixer.stopAllAction();
      this.active = src;
      this.action = null;
    }
    if (this.action === next && !oneShot) return true;

    // tout estomper, y compris une pose maintenue (clampWhenFinished)
    for (const a of Object.values(src.actions)) {
      if (a !== next && (a.isRunning() || a.getEffectiveWeight() > 0)) a.fadeOut(fade);
    }

    next.reset();
    next.enabled = true;
    next.setEffectiveTimeScale(1);
    next.setEffectiveWeight(1);
    next.setLoop(oneShot ? THREE.LoopOnce : THREE.LoopRepeat, oneShot ? 1 : Infinity);
    next.clampWhenFinished = oneShot;
    next.fadeIn(fade).play();

    this.action = next;
    this.oneShot = oneShot;
    this.scaleY = this.vrmHeight && src.height ? this.vrmHeight / src.height : 1;

    // yaw auto : la pose de repos du VRM doit coïncider avec celle de l'anim
    if (this.vrmForward !== null && src.forward) {
      const srcFwd = Math.atan2(src.forward.x, src.forward.z);
      this.setYaw(THREE.MathUtils.radToDeg(this.vrmForward - srcFwd));
    }
    this.computeFrameYaw();
    return true;
  }

  setYaw(deg) {
    this.yaw = THREE.MathUtils.degToRad(deg);
    this._yawQ.setFromAxisAngle(new THREE.Vector3(0, 1, 0), this.yaw);
    this._yawInv.copy(this._yawQ).invert();
    this.computeFrameYaw();
  }

  // Direction "avant" réelle de l'animation : vecteur pied -> orteils,
  // corrigé du yaw. Permet de savoir de quel angle le corps visuel diffère
  // de l'orientation du conteneur (et donc dans quel sens elle marche).
  computeFrameYaw() {
    this.frameYaw = 0;
    const fwd = this.active && this.active.forward;
    if (!fwd) return 0;
    const cy = Math.cos(this.yaw), sy = Math.sin(this.yaw);
    const cx = fwd.x * cy + fwd.z * sy;      // rotation du vecteur par le yaw
    const cz = -fwd.x * sy + fwd.z * cy;
    this.frameYaw = Math.atan2(cx, cz);
    return this.frameYaw;
  }

  update(dt) {
    const src = this.active;
    if (!src || !this.vrm) return;

    src.mixer.update(dt * this.speed);
    src.scene.updateMatrixWorld(true);

    // purge les actions estompées (sinon une pose maintenue garde du poids)
    for (const a of Object.values(src.actions)) {
      if (a !== this.action && !a.isRunning() && a.getEffectiveWeight() === 0) a.stop();
    }

    for (const name of ORDER) {
      const node = this.nodes[name];
      const bone = src.pairs[name];
      if (!node || !bone) continue;
      if (!this.fingers && isFinger(name)) { node.quaternion.identity(); continue; }

      // delta world = rot animée * rot repos⁻¹
      bone.getWorldQuaternion(this._q1);
      this._q2.copy(this._q1).multiply(src.restInv[name]);

      // correction d'orientation globale (conjugaison)
      if (this.yaw) {
        this._q2.premultiply(this._yawQ);
        this._q2.multiply(this._yawInv);
      }

      // clavicules : le repos Mixamo les rentre dans le torse -> on les rapproche du repos
      if (name === 'leftShoulder' || name === 'rightShoulder') {
        _q3.identity().slerp(this._q2, this.shoulders);
        this._q2.copy(_q3);
      }

      // bras légèrement ouverts en monde : épaules plus larges, bras moins collés
      if (name === 'leftUpperArm' || name === 'rightUpperArm') {
        const sgn = name === 'rightUpperArm' ? 1 : -1;
        _q3.setFromAxisAngle(AXIS_Z, sgn * THREE.MathUtils.degToRad(this.armWiden));
        this._q2.premultiply(_q3);
      }

      // écartement latéral amorti sur TOUTE la jambe : cuisse, tibia et pied,
      // sinon le genou se casse et le tibia repart vers l'extérieur.
      if (LEG_UPPER.test(name) || LEG_LOWER.test(name)) {
        _eul.setFromQuaternion(this._q2, 'XYZ');
        _eul.z *= this.legAbduct;
        _eul.y *= this.legTwist;
        this._q2.setFromEuler(_eul);
      }
      if (LEG_FOOT.test(name)) {
        _eul.setFromQuaternion(this._q2, 'YXZ');
        _eul.y *= this.footYaw;
        _eul.z *= this.footYaw;
        this._q2.setFromEuler(_eul);
      }

      // local = parentDelta⁻¹ * delta
      const par = PARENT[name];
      const pd = (par && this._hasDelta[par]) ? this.deltaWorld[par] : this._ident;
      const local = this._q1.copy(pd).invert().multiply(this._q2);
      node.quaternion.copy(local);
      this.deltaWorld[name].copy(this._q2);
      this._hasDelta[name] = true;
    }

    // montée/descente du bassin (rebond, respiration) — pas de translation avant/arrière
    const hips = this.nodes['hips'];
    const srcHips = src.pairs['hips'];
    if (hips && srcHips) {
      srcHips.getWorldPosition(this._v1);
      const dy = (this._v1.y - src.restHips.y) * this.scaleY;
      const rp = this.hipsRestPos || { x: 0, y: 0.8, z: 0 };
      hips.position.set(rp.x, rp.y + THREE.MathUtils.clamp(dy, -0.25, 0.25), rp.z);
    }
  }
}
