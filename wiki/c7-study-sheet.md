# C-7 study sheet: numbers to memorize

For Fran's California C-7 (Low Voltage Systems) trade test. Built 2026-10-07 from the industry
cabling standards (TIA-568, 569, 606, 607) and the CSLB C-7 study guide topics. Check anything
that matters against the official CSLB C-7 study guide.

---

## 1. What the C-7 covers
- Communication and low voltage systems that are **energy limited, 91 volts or less**.
- **Not** fire alarm systems. (That trick question shows up.)
- Covers: data, phone, VoIP, CCTV/cameras, access control, sound, building automation, fiber.

## 2. Cable lengths (copper)
| Item | Number |
|---|---|
| Horizontal cable, panel to jack (permanent link) | **90 m / 295 ft** |
| Patch cords, both ends combined | **10 m / 33 ft** |
| Whole channel, end to end | **100 m / 328 ft** |
| Cat 6 running 10 Gig | up to **55 m** |
| Cat 6A running 10 Gig | full **100 m** |

## 3. Cable categories
| Cable | Bandwidth | Typical speed |
|---|---|---|
| Cat 5e | 100 MHz | 1 Gig |
| Cat 6 | 250 MHz | 1 Gig (10 Gig to 55 m) |
| Cat 6A | 500 MHz | 10 Gig to 100 m |

**Impedance:** UTP (Cat 5e/6/6A) = **100 ohms** · video coax RG-59/RG-6 = **75 ohms** · RG-58 = 50 ohms

## 4. Installing copper
| Rule | Number |
|---|---|
| Most you can untwist a pair at termination | **½ inch** |
| Bend radius, 4-pair UTP | **4 × cable diameter** |
| Bend radius, multi-pair backbone | **10 × cable diameter** |
| Pulling tension, 4-pair UTP | **25 lbf** max |
| J-hook / support spacing | every **5 ft** max |

**Jacket ratings, best to least:** Plenum (**CMP**) > Riser (**CMR**) > General (**CM**).
Air-handling space above a ceiling = plenum cable required.

## 5. Pinouts (pins 1 to 8)
| Pin | T568**A** | T568**B** |
|---|---|---|
| 1 | white-green | **white-orange** |
| 2 | green | **orange** |
| 3 | white-orange | white-green |
| 4 | blue | blue |
| 5 | white-blue | white-blue |
| 6 | orange | green |
| 7 | white-brown | white-brown |
| 8 | brown | brown |

**Memory trick:** A and B only swap the **orange and green** pairs. Blue and brown never move.
**B starts with "Bright Orange."**

## 6. PoE (power over Ethernet)
| Standard | Name | Watts at the switch |
|---|---|---|
| 802.3af | PoE | **15.4 W** |
| 802.3at | PoE+ | **30 W** |
| 802.3bt Type 3 | PoE++ | **60 W** |
| 802.3bt Type 4 | PoE++ | **90 W** |

## 7. Fiber
| Type | Jacket color | Light (wavelength) |
|---|---|---|
| Single-mode | **yellow** | 1310 / 1550 nm |
| Multimode OM1/OM2 | orange | 850 / 1300 nm |
| Multimode OM3/OM4 | **aqua** | 850 / 1300 nm |
| Multimode OM5 | lime green | 850 to 953 nm |

- Fiber bend radius: **10 × diameter** at rest, **20 × diameter** while pulling.
- **Always inspect and clean** connectors before connecting or testing.

## 8. Test tools: which does what
| Tool | Use it to |
|---|---|
| Tone generator and probe | **find** which cable is which |
| Wiremap / continuity tester | check pins are wired right (opens, shorts, split pairs) |
| Cable certifier | prove a link **meets the standard** (needed for warranties) |
| Light source + power meter | measure fiber **loss** end to end |
| **OTDR** | find **where** along a fiber a break or bad splice is |
| Visual fault locator (red light) | see a break or bad connector **nearby** |
| Fiber inspection scope | check a connector end is clean |

## 9. Estimating cable
1. Average run = (shortest run + longest run) ÷ 2, plus about **10% slack**.
2. Runs per box = 1,000 ft ÷ average run, **rounded down** (no splicing).
3. Boxes = number of drops ÷ runs per box, **rounded up**.

**Example:** 40 drops at 150 ft → 1,000 ÷ 150 = 6.67 → **6 runs per box** → 40 ÷ 6 = 6.67 → **7 boxes**.

## 10. Grounding and bonding
- Main telecom ground bar: **TMGB**. Ground bar in each telecom room: **TGB**.
- Bonding conductor: **6 AWG copper minimum**.
- Bond racks, cable trays and shielded cable to the ground bar.

## 11. The standards, by name
| Standard | Covers |
|---|---|
| TIA-568 | cabling and performance |
| TIA-569 | pathways and spaces (conduit, trays, rooms) |
| TIA-606 | labeling and records |
| TIA-607 | grounding and bonding |

## 12. Safety (Cal/OSHA)
| Rule | Number |
|---|---|
| Extension ladder above the landing | **3 ft** |
| Ladder angle | **1 ft out for every 4 ft up** |
| Fall protection in construction | at **7½ ft** |
| Trench needs shoring | **5 ft** deep or more |
| Written safety plan (IIPP) | **every** employer |

## 13. Project closeout checklist
- Test results for every link (certifier printouts).
- As-built drawings showing where everything really went.
- Labels on both ends of every cable, and a labeling list (TIA-606).
- Manufacturer warranty paperwork.
- Training the customer, and handing over manuals and equipment logins.
