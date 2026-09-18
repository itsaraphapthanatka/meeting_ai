#!/usr/bin/env bash
# ติดตั้ง systemd unit ของ worker โดยเติมค่าที่ต่างกันทุกเครื่องให้อัตโนมัติ (BACKLOG #28)
#
#   ./install-worker-service.sh                 # แสดง unit ที่จะได้ (ไม่เขียนอะไร)
#   ./install-worker-service.sh --install       # เขียนจริง (ต้อง sudo)
#   ./install-worker-service.sh --install --api https://xxx.vercel.app --max-bots 6
#
# --max-bots: ย้ายจาก unit เดิมที่ฝังเลขไว้ ต้องส่งเลขเดิมมาด้วย ไม่งั้นจะได้ค่าตั้งต้น 3
#
# ทับค่าที่เดาได้เองด้วยตัวแปรสภาพแวดล้อม (เทสต์ใช้ทางนี้):
#   MAI_SVC_USER  MAI_SVC_GROUP  MAI_SVC_HOME  MAI_SVC_ROOT  MAI_SVC_ENVFILE
set -eu

TEMPLATE_NAME="meeting-ai-worker.service.in"
UNIT_NAME="meeting-ai-worker.service"
EXAMPLE_NAME="meeting-ai-worker.env.example"

# โฟลเดอร์โปรเจกต์ = ที่ที่สคริปต์นี้อยู่ ไม่ใช่ที่ที่ถูกเรียก
here="$(cd "$(dirname "$0")" && pwd)"

root="${MAI_SVC_ROOT:-$here}"
# sudo ทำให้ id -un กลายเป็น root ซึ่งเป็นผู้ใช้ที่ **ไม่ควร** รัน worker
user="${MAI_SVC_USER:-${SUDO_USER:-$(id -un)}}"
group="${MAI_SVC_GROUP:-$(id -gn "$user" 2>/dev/null || echo "$user")}"
home="${MAI_SVC_HOME:-$(eval echo "~$user")}"
envfile="${MAI_SVC_ENVFILE:-/etc/default/meeting-ai-worker}"
unit_dir="${MAI_SVC_UNIT_DIR:-/etc/systemd/system}"

do_install=0
api=""
max_bots=""
while [ $# -gt 0 ]; do
    case "$1" in
        --install) do_install=1 ;;
        --print) do_install=0 ;;
        --api) shift; api="${1:-}" ;;
        --max-bots) shift; max_bots="${1:-}" ;;
        --env-file) shift; envfile="${1:-}" ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "ไม่รู้จักตัวเลือก: $1" >&2; exit 2 ;;
    esac
    shift
done

template="$here/$TEMPLATE_NAME"
[ -f "$template" ] || { echo "ไม่พบ $template" >&2; exit 1; }
[ -x "$root/mai" ] || echo "เตือน: ไม่พบ (หรือรันไม่ได้) $root/mai" >&2

# แทนค่าด้วยการตัดสตริงเอง ไม่ใช่ sed และไม่ใช่ ${s//pat/rep}
#
# sed: ค่าที่มี `/` หรือ `&` ทำให้คำสั่งเพี้ยน
# ${s//pat/rep}: **bash 5.2 ขึ้นไปตีความ `&` ในฝั่งแทนที่ว่าเป็น "ข้อความที่แมตช์"**
#   วัดแล้วบน bash 5.2.37: v="a&b"; s="X@T@Y"; "${s//@T@/$v}" ได้ `Xa@T@bY` ไม่ใช่ `Xa&bY`
#   โฟลเดอร์ชื่อ `/srv/a&b` จึงกลายเป็น `/srv/a@ROOT@b` เงียบ ๆ — unit ที่ได้ชี้ผิดที่
# วิธีข้างล่างต่อสตริงตรง ๆ จึงไม่มีอักขระไหนพิเศษเลย และไม่ขึ้นกับรุ่นของ bash
subst() {
    local s=$1 tok=$2 val=$3 out=""
    while [ "${s#*"$tok"}" != "$s" ]; do
        out="$out${s%%"$tok"*}$val"
        s="${s#*"$tok"}"
    done
    printf '%s%s' "$out" "$s"
}

render() {
    local line
    while IFS= read -r line || [ -n "$line" ]; do
        line="$(subst "$line" "@USER@" "$user")"
        line="$(subst "$line" "@GROUP@" "$group")"
        line="$(subst "$line" "@HOME@" "$home")"
        line="$(subst "$line" "@ROOT@" "$root")"
        line="$(subst "$line" "@ENVFILE@" "$envfile")"
        printf '%s\n' "$line"
    done < "$template"
}

if [ "$do_install" -eq 0 ]; then
    render
    echo "# ---- ยังไม่ได้เขียนอะไร ใส่ --install เพื่อเขียนลง $unit_dir/$UNIT_NAME ----" >&2
    exit 0
fi

mkdir -p "$unit_dir"
render > "$unit_dir/$UNIT_NAME"
echo "เขียน $unit_dir/$UNIT_NAME แล้ว (user=$user root=$root)"

if [ ! -f "$envfile" ]; then
    mkdir -p "$(dirname "$envfile")"
    cp "$here/$EXAMPLE_NAME" "$envfile"
    echo "สร้าง $envfile จากตัวอย่าง — **ต้องแก้ MAI_API ก่อน** ไม่งั้น worker จะไม่เจอเซิร์ฟเวอร์"
fi
# เขียนทับบรรทัดเดิม ไม่ต่อท้ายซ้ำ ๆ ทุกครั้งที่รัน (ค่าสุดท้ายชนะแบบเดาไม่ได้)
set_env() {
    local key=$1 value=$2 tmp="$envfile.tmp.$$"
    { grep -v "^$key=" "$envfile" || true; echo "$key=$value"; } > "$tmp"
    mv "$tmp" "$envfile"
    echo "ตั้ง $key=$value"
}

[ -n "$api" ] && set_env MAI_API "$api"
# ค่าเริ่มต้นในไฟล์ตัวอย่างคือ 3 ซึ่งอาจ **ต่ำกว่า** ที่เครื่องนั้นเคยตั้งไว้ใน unit เดิม
# (เครื่อง GB10 ใช้ 6) ย้ายมาใช้ไฟล์ตัวแปรแล้วลืมข้อนี้ = จำนวนบอทพร้อมกันลดลงเงียบ ๆ
[ -n "$max_bots" ] && set_env MAI_MAX_BOTS "$max_bots"

command -v systemctl >/dev/null 2>&1 || { echo "ไม่มี systemctl — ข้ามการ reload"; exit 0; }
# reload ล้มไม่ใช่เหตุให้ทั้งสคริปต์ล้ม: ไฟล์ถูกเขียนไปแล้ว และ reload ต้องสิทธิ์ root
# (รันแบบไม่ใช่ root จะได้ "Interactive authentication required" ซึ่งไม่ได้แปลว่าติดตั้งไม่สำเร็จ)
systemctl daemon-reload || echo "daemon-reload ไม่ผ่าน — รัน sudo systemctl daemon-reload เอง"
echo
echo "ขั้นต่อไป:"
echo "  sudo systemctl enable --now $UNIT_NAME"
echo "  systemctl status $UNIT_NAME"
echo "  journalctl -u $UNIT_NAME -f"
