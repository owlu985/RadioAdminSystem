@bp.route('/api/now')
def now_playing():
	show = get_current_show()
	if not show:
		return {"status": "off_air"}
	return {
		"show": show.show_name,
		"host": f"{show.host_first_name} {show.host_last_name}",
		"ends_at": show.end_time.strftime("%H:%M")
	}
